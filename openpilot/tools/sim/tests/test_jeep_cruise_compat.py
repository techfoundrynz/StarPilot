"""Identical Jeep CAN scenarios for 6.x and 7.x, including compiled panda safety.

Copy this file into either checkout and run with that checkout's Python and
dependencies. Compatibility adapters only translate API signatures; vehicle,
event, state-machine, controller and native safety behavior come from the checkout.
The scenario covers factory CarParams, CAN parsing, car events, the engagement
state machine, controller output and compiled safety RX/TX checks. It does not
exercise manager/pandad startup, actual firmware, or selfdrived's full fault-event
and interprocess transport pipeline. No device or firmware is contacted.

Run with pytest in a built Linux checkout. For 6.x, copy the file to
tools/sim/tests/ and build its libsafety.so first; v7 builds libsafety on import.
"""

from collections.abc import Callable
import inspect
from types import SimpleNamespace
from typing import Any
import unittest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from opendbc.safety.tests.libsafety import libsafety_py

LEGACY = 'starpilot_toggles' in inspect.signature(CarInterface.get_params).parameters
if LEGACY:
  from cereal import custom
  from openpilot.selfdrive.car.car_specific import CarSpecificEvents as CarEvents
else:
  from openpilot.selfdrive.car.car_events import CarEvents

from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.state import StateMachine


def legacy_call(call: Callable[..., Any], *args: Any) -> Any:
  """The 6.x APIs have additional arguments absent from the current type hints."""
  return call(*args)


class JeepCruiseSimulation:
  def __init__(self, candidate, *, brake_hold=False, bypass=False):
    self.toggles = SimpleNamespace(jeep_brake_hold=brake_hold)
    fp = gen_empty_fingerprint()
    if bypass:
      fp[0][0x4FF] = 8
    args = (candidate, fp, [], False, False, False)
    self.cp = legacy_call(CarInterface.get_params, *args, self.toggles) if LEGACY else CarInterface.get_params(*args)
    if LEGACY:
      fpcp = custom.StarPilotCarParams()
      if bypass:
        from opendbc.car.chrysler.values import ChryslerStarPilotFlags
        fpcp.flags |= int(ChryslerStarPilotFlags.NO_MIN_STEERING_SPEED)
      self.ci = legacy_call(CarInterface, self.cp, fpcp)
    else:
      from opendbc.car.chrysler.brake_hold import apply_brake_hold
      apply_brake_hold(self.cp, brake_hold)
      self.ci = CarInterface(self.cp)
    self.previous = structs.CarState()
    self.events = CarEvents(self.cp)
    self.machine = StateMachine()
    self.enabled = False
    self.cc = structs.CarControl()
    self.tick = 0
    self.steering_commands = 0
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)  # Ordinary stock-ACC engagement; AOL off.
    assert self.safety.set_safety_hooks(int(self.cp.safetyConfigs[0].safetyModel.raw), self.cp.safetyConfigs[0].safetyParam) == 0
    self.update([])  # Let the real CarState register the messages it reads.
    self.packers = {bus: CANPacker(parser.dbc_name) for bus, parser in self.ci.can_parsers.items()}

  def update(self, packets):
    result = legacy_call(self.ci.update, packets, self.toggles) if LEGACY else self.ci.update(packets)
    return result[0] if LEGACY else result

  def step(self, *, active=False, speed=15., set_speed=60, button=None, corrupt=None, omit=None):
    self.tick += 1
    now = self.tick * 10_000_000
    self.safety.set_timer(now // 1000)
    values: dict[str, dict[str, float]] = {
      'DAS_3': {'ACC_AVAILABLE': 1, 'ACC_ACTIVE': int(active)},
      'DAS_4': {'ACC_SET_SPEED_KPH': set_speed, 'ACC_STATE': 3},
      'GEAR': {'PRNDL': 4, 'COUNTER': self.tick % 16},
      'SPEED_1': {'SPEED_LEFT': speed, 'SPEED_RIGHT': speed},
      'EPS_2': {'EPS_TORQUE_MOTOR': 0, 'LKAS_STATE': 1},
      'CRUISE_BUTTONS': {button: 1} if button else {},
    }
    frames = []
    for bus, parser in self.ci.can_parsers.items():
      for address in sorted(parser.addresses):
        name = parser.dbc.addr_to_msg[address].name
        # Safety-critical sources run at their actual 50/100 Hz cadence.
        if name in ('DAS_3', 'ECM_5', 'ESP_1') and self.tick % 2 == 0:
          continue
        if name == omit:
          continue
        addr, data, physical_bus = self.packers[bus].make_can_msg(name, parser.bus, values.get(name, {}))
        if name == corrupt:
          data = data[:-1] + bytes([data[-1] ^ 0xFF])
        frame = (addr, data, physical_bus)
        frames.append(frame)
        self.safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, physical_bus, data))
    if self.tick % 100 == 0:
      (self.safety.safety_tick_current_safety_config if LEGACY else self.safety.safety_tick)()
    cs = self.update([(now, frames)])
    events = self.events.update(cs, self.previous, self.cc)
    self.previous = cs
    enabled, active_axes = legacy_call(self.machine.update, events, Events(), False) if LEGACY else self.machine.update(events)
    self.enabled = enabled
    self.cc.enabled = enabled
    self.cc.latActive = active_axes and cs.vEgo > self.cp.minSteerSpeed
    self.cc.actuators.torque = 0.1 if self.cc.latActive else 0.
    if LEGACY:
      actuators, sent = legacy_call(self.ci.apply, self.cc.as_reader(), now, self.toggles)
    else:
      actuators, sent = self.ci.apply(self.cc.as_reader(), now)
    self.steering_commands += int(abs(actuators.torque) > 0.)
    rejected = [hex(addr) for addr, data, bus in sent
                if not self.safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))]
    return cs, events, rejected


class TestJeepCruiseCompatibility(unittest.TestCase):
  def assert_healthy(self, sim, *, active, speed=15., set_speed=60, button=None):
    cs, events, rejected = sim.step(active=active, speed=speed, set_speed=set_speed, button=button)
    self.assertTrue(cs.canValid, (sim.tick, 'parser invalid'))
    self.assertFalse(cs.canTimeout, sim.tick)
    self.assertTrue(sim.safety.safety_config_valid(), (sim.tick, 'native RX invalid'))
    self.assertFalse(sim.safety.get_relay_malfunction(), sim.tick)
    self.assertEqual(sim.safety.get_controls_allowed(), active, (sim.tick, 'native control permission'))
    self.assertEqual(sim.enabled, active, (sim.tick, 'host engagement', events.names))
    self.assertEqual(cs.cruiseState.enabled, active)
    self.assertAlmostEqual(cs.cruiseState.speed * 3.6, set_speed, places=2)
    self.assertEqual(rejected, [], (sim.tick, 'native TX rejected'))

  def test_set_increase_speed_cancel_and_reengage(self):
    for candidate in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019):
      for hold in (False, True):
        for bypass in (False, True):
          with self.subTest(candidate=candidate, brake_hold=hold, bypass=bypass):
            sim = JeepCruiseSimulation(candidate, brake_hold=hold, bypass=bypass)
            for _ in range(120):
              sim.step()
            # Physical SET alone cannot manufacture stock ACC engagement.
            for _ in range(20):
              self.assert_healthy(sim, active=False, button='ACC_Decel')
            # SET activates stock ACC; hold RES/+ while the actual speed crosses
            # the newer Jeep's minimum steering speed (17.5 m/s).
            for tick in range(500):
              target = min(100, 60 + tick // 10)
              self.assert_healthy(sim, active=True, speed=min(25., 15. + tick / 30), set_speed=target,
                                  button='ACC_Decel' if tick < 10 else 'ACC_Accel' if tick < 410 else None)
            for _ in range(30):
              self.assert_healthy(sim, active=False, speed=25., set_speed=100, button='ACC_Cancel')
            for _ in range(250):
              self.assert_healthy(sim, active=True, speed=25., set_speed=100, button='ACC_Resume')
            self.assertGreater(sim.steering_commands, 0, 'Must exercise nonzero steering, not only cruise state')

  def test_bad_checksum_is_detected(self):
    sim = JeepCruiseSimulation(CAR.JEEP_GRAND_CHEROKEE_2019)
    for _ in range(120):
      sim.step()
    for _ in range(50):
      self.assert_healthy(sim, active=True, speed=25.)
    for _ in range(120):
      sim.step(active=True, speed=25., corrupt='EPS_2')
    self.assertFalse(sim.safety.safety_config_valid())
    self.assertFalse(sim.safety.get_controls_allowed())

  def test_missing_acc_source_is_detected(self):
    sim = JeepCruiseSimulation(CAR.JEEP_GRAND_CHEROKEE_2019)
    for _ in range(120):
      sim.step()
    for _ in range(50):
      self.assert_healthy(sim, active=True, speed=25.)
    for _ in range(230):
      sim.step(active=True, speed=25., omit='DAS_3')
    self.assertFalse(sim.safety.safety_config_valid())
    self.assertFalse(sim.safety.get_controls_allowed())
