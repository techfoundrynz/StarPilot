import hashlib
import importlib.util
from pathlib import Path
import tarfile

import pytest


spec = importlib.util.spec_from_file_location('package_device_build', Path(__file__).with_name('package_device_build.py'))
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.fixture
def device_tree(tmp_path, monkeypatch):
  root = tmp_path / 'repo'
  monkeypatch.setattr(packager, 'ROOT', root)
  header = bytearray(64)
  header[:6] = b'\x7fELF\x02\x01'
  header[18:20] = (183).to_bytes(2, 'little')
  for relative in packager.REQUIRED_BINARIES:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header)
  (root / 'prebuilt').touch()
  firmware = root / 'panda/board/obj'
  firmware.mkdir(parents=True)
  (firmware / 'panda.bin.signed').write_bytes(b'firmware')
  (firmware / 'bootstub.panda.bin').write_bytes(b'bootstub')
  return root


def test_only_binary_allowlist_is_archived(device_tree, tmp_path):
  (device_tree / 'private.txt').write_text('must not be exported')
  (device_tree / 'source.py').write_text('print("source")')
  archive = packager.package(tmp_path / 'output')
  with tarfile.open(archive) as bundle:
    assert set(bundle.getnames()) == set(packager.REQUIRED_BINARIES) | {
      'prebuilt', 'panda/board/obj/panda.bin.signed', 'panda/board/obj/bootstub.panda.bin',
    }
  digest = hashlib.sha256(archive.read_bytes()).hexdigest()
  assert (archive.parent / 'SHA256SUMS').read_text() == f'{digest}  {archive.name}\n'


@pytest.mark.parametrize('contents', [b'', b'not ELF', b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\0'])
def test_rejects_empty_corrupt_and_host_binaries(device_tree, tmp_path, contents):
  (device_tree / packager.REQUIRED_BINARIES[0]).write_bytes(contents)
  with pytest.raises(ValueError, match='AArch64'):
    packager.package(tmp_path / 'output')
  assert not (tmp_path / 'output/starpilot-device.tar.gz').exists()


def test_requires_all_runtime_binaries(device_tree, tmp_path):
  (device_tree / packager.REQUIRED_BINARIES[-1]).unlink()
  with pytest.raises(FileNotFoundError):
    packager.package(tmp_path / 'output')


def test_requires_matching_bootstub(device_tree, tmp_path):
  (device_tree / 'panda/board/obj/bootstub.panda.bin').unlink()
  with pytest.raises(ValueError, match='firmware'):
    packager.package(tmp_path / 'output')
