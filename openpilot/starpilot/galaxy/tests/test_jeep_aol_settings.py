"""Jeep AOL settings admission and actual Galaxy save transactions."""
from dataclasses import replace
import tempfile
import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
from openpilot.starpilot.galaxy.tests.settings_http_fixture import MutableContext
from openpilot.starpilot.galaxy.vehicle_configuration import configuration_context
from openpilot.starpilot.vehicle_selection import encode
import json


class TestJeepAolSettings(unittest.TestCase):
  def test_actual_gateway_can_enable_2021_jeep_with_and_without_bypass(self):
    for bypass in (False, True):
      for parked in (False, True):
        with self.subTest(bypass=bypass, parked=parked), tempfile.TemporaryDirectory() as directory:
          params = Params(directory)
          params.put_bool('AlwaysOnLateral', False, block=True)
          fingerprint = gen_empty_fingerprint()
          if bypass:
            fingerprint[0][0x4FF] = 8
          cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, fingerprint, [], False, False, False)
          context = MutableContext(cp)
          context.value = replace(context.value, parked=parked)
          gateway = SettingsGateway(params, context)
          page = gateway.page('aol', 'test-session', b'test-generation')
          index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
          self.assertTrue(page['rows'][index]['available'], page['rows'][index]['reason'])
          self.assertEqual(page['rows'][index]['value'], 'Off')
          intent = gateway.preview(page['view'], index, 1, 'test-session', b'test-generation')
          self.assertTrue(gateway.confirm(intent['intent'], 'test-session', b'test-generation'))
          self.assertTrue(params.get_bool('AlwaysOnLateral'))

  def test_manual_2021_selection_can_enable_aol_offroad(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('VehicleSelection', json.loads(encode(str(CAR.JEEP_GRAND_CHEROKEE_2019))), block=True)
      params.put_bool('OpenpilotEnabledToggle', True, block=True)
      params.put_bool('AlwaysOnLateral', False, block=True)
      cp, raw = configuration_context(params, None, None)
      assert cp is not None
      context = MutableContext(cp)
      context.value = AuthorityContext(True, cp, raw, configuration_vehicle=True)
      gateway = SettingsGateway(params, context)
      page = gateway.page('aol', 'test-session', b'test-generation')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
      self.assertTrue(page['rows'][index]['available'], page['rows'][index]['reason'])
      intent = gateway.preview(page['view'], index, 1, 'test-session', b'test-generation')
      self.assertTrue(gateway.confirm(intent['intent'], 'test-session', b'test-generation'))
      self.assertTrue(params.get_bool('AlwaysOnLateral'))

  def test_missing_vehicle_explains_why_aol_is_disabled(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('AlwaysOnLateral', False, block=True)
      context = MutableContext(None)
      gateway = SettingsGateway(params, context)
      row = next(row for row in gateway.page('aol', 'test-session', b'test-generation')['rows']
                 if row['label'] == 'Enable Always On Lateral')
      self.assertFalse(row['available'])
      self.assertIn('Connect a supported vehicle', row['reason'])
