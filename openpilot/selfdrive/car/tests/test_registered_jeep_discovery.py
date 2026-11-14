"""Selected Jeep discovery must collect live evidence without switching CAN2 routing."""
from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest

from opendbc.car import car_helpers, fw_versions, gen_empty_fingerprint
from opendbc.car.structs import CarParams

JEEP = 'JEEP_GRAND_CHEROKEE_2019'
VIN = '1C4RJFBG0KC000001'


@pytest.fixture(autouse=True)
def discovery_environment(monkeypatch):
  for key in ('FINGERPRINT', 'SKIP_FW_QUERY', 'DISABLE_FW_CACHE'):
    monkeypatch.setenv(key, '')


def test_selected_jeep_collects_live_vin_fw_and_can_without_obd_mux():
  callbacks = []
  queries = []
  layout = gen_empty_fingerprint()
  layout[0][0x4FF] = 4
  with ExitStack() as stack:
    vin = stack.enter_context(mock.patch.object(car_helpers, 'get_vin', return_value=(0x7E8, 0, VIN)))
    present = stack.enter_context(mock.patch.object(car_helpers, 'get_present_ecus'))
    ordered = stack.enter_context(mock.patch.object(car_helpers, 'get_fw_versions_ordered'))
    can = stack.enter_context(mock.patch.object(car_helpers, 'can_fingerprint', return_value=(None, layout)))
    factory = mock.Mock()
    factory.get_params.return_value = CarParams(carFingerprint=JEEP, brand='chrysler')
    stack.enter_context(mock.patch.dict(car_helpers.interfaces, {JEEP: factory}))
    query_class = stack.enter_context(mock.patch.object(fw_versions, 'IsoTpParallelQuery'))

    def response(*args, **kwargs):
      assert args[2] == 0, 'Selected Jeep discovery must not send on CAN2/OBD bus1'
      queries.append((args[2], args[3], args[4]))
      query = mock.Mock()
      query.get_data.return_value = {args[3][0]: b'observed-chrysler-firmware'}
      return query

    query_class.side_effect = response
    car_helpers.get_car(mock.Mock(return_value=[]), mock.Mock(), callbacks.append, False, False, forced_candidate=JEEP)
  assert callbacks and set(callbacks) == {False}
  vin.assert_called_once()
  assert vin.call_args.args[2] == (0,)
  present.assert_not_called()
  ordered.assert_not_called()
  can.assert_called_once()
  factory.get_params.assert_called_once()
  assert factory.get_params.call_args.args[1] is layout
  cp = factory.call_args.args[0]
  assert cp.carFingerprint == JEEP and cp.carVin == VIN
  assert cp.fingerprintSource == CarParams.FingerprintSource.fixed
  assert cp.carFw and queries
  assert all(fw.brand == 'chrysler' and fw.bus == 0 and fw.fwVersion == b'observed-chrysler-firmware' for fw in cp.carFw)


@pytest.mark.parametrize('drift', ['configured_bus1', 'configured_empty', 'effective_bus1', 'effective_empty'])
def test_query_topology_drift_rejects_scoped_discovery_before_io(drift):
  config = fw_versions.FW_QUERY_CONFIGS['chrysler']
  requests = list(config.requests)
  effective = list(fw_versions.REQUESTS)
  if drift == 'configured_bus1':
    requests[0] = replace(requests[0], bus=1)
  elif drift == 'configured_empty':
    requests = []
  elif drift == 'effective_bus1':
    effective.append(('chrysler', config, replace(config.requests[0], bus=1)))
  else:
    effective = [entry for entry in effective if entry[0] != 'chrysler']
  recv, send, mux = mock.Mock(), mock.Mock(), mock.Mock()
  with mock.patch.dict(fw_versions.FW_QUERY_CONFIGS, {'chrysler': replace(config, requests=requests, extra_ecus=[])}), \
       mock.patch.object(fw_versions, 'REQUESTS', effective):
    with pytest.raises(ValueError, match='Jeep discovery topology'):
      car_helpers.fingerprint(recv, send, mux, None, known_jeep_bus0=True)
  recv.assert_not_called()
  send.assert_not_called()
  mux.assert_not_called()


@pytest.mark.parametrize('selected', [None, 'JEEP_GRAND_CHEROKEE', 'CHRYSLER_PACIFICA_2018'])
def test_scope_does_not_apply_to_automatic_or_other_vehicle_selection(selected):
  candidate = selected or JEEP
  result = (candidate, gen_empty_fingerprint(), VIN, [], CarParams.FingerprintSource.can, True)
  factory = mock.Mock()
  factory.get_params.return_value = CarParams(carFingerprint=candidate, brand='chrysler')
  with mock.patch.object(car_helpers, 'fingerprint', return_value=result) as discover, \
       mock.patch.dict(car_helpers.interfaces, {candidate: factory}):
    car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, forced_candidate=selected)
  assert discover.call_args.kwargs == {}


@pytest.mark.parametrize('brand,candidate,matching', [('chrysler', JEEP, True), ('ford', JEEP, False),
                                                   ('chrysler', 'JEEP_GRAND_CHEROKEE', False)])
def test_selected_jeep_cannot_reuse_another_vehicle_cache(brand, candidate, matching):
  cached = CarParams(brand=brand, carFingerprint=candidate, carVin=VIN)
  result = (JEEP, gen_empty_fingerprint(), VIN, [], CarParams.FingerprintSource.can, True)
  factory = mock.Mock()
  factory.get_params.return_value = CarParams(carFingerprint=JEEP, brand='chrysler')
  with mock.patch.object(car_helpers, 'fingerprint', return_value=result) as discover, \
       mock.patch.dict(car_helpers.interfaces, {JEEP: factory}):
    car_helpers.get_car(mock.Mock(), mock.Mock(), mock.Mock(), False, False, cached_params=cached, forced_candidate=JEEP)
  assert discover.call_args.kwargs == {'known_jeep_bus0': True}
  assert discover.call_args.args[3] is (cached if matching else None)


@pytest.mark.parametrize('skip,disable_cache', [('', ''), ('1', ''), ('', '1')])
def test_scoped_discovery_preserves_skip_cache_and_live_fingerprinting(skip, disable_cache, monkeypatch):
  monkeypatch.setenv('SKIP_FW_QUERY', skip)
  monkeypatch.setenv('DISABLE_FW_CACHE', disable_cache)
  cached_fw = CarParams.CarFw(brand='chrysler', bus=0, address=0x7E0, fwVersion=b'cached')
  cached = CarParams(brand='chrysler', carFingerprint=JEEP, carVin=VIN, carFw=[cached_fw])
  live_fw = [CarParams.CarFw(brand='chrysler', bus=0, address=0x7E0, fwVersion=b'live')]
  callbacks = []
  with mock.patch.object(car_helpers, 'get_vin', return_value=(0x7E8, 0, VIN)) as vin, \
       mock.patch.object(car_helpers, 'get_fw_versions', return_value=live_fw) as firmware, \
       mock.patch.object(car_helpers, 'can_fingerprint', return_value=(None, gen_empty_fingerprint())) as can:
    result = car_helpers.fingerprint(mock.Mock(return_value=[]), mock.Mock(), callbacks.append, cached, known_jeep_bus0=True)
  assert callbacks == ([False, False] if disable_cache else [False])
  can.assert_called_once()
  if skip:
    vin.assert_not_called()
    firmware.assert_not_called()
    assert result[3] == []
  elif disable_cache:
    assert vin.call_args.args[2] == (0,)
    assert firmware.call_args.kwargs == {'query_brand': 'chrysler', 'normal_bus_only': True}
    assert result[3] == live_fw
  else:
    vin.assert_not_called()
    firmware.assert_not_called()
    assert result[3][0].fwVersion == b'cached'
