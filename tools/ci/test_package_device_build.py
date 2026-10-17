import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest


spec = importlib.util.spec_from_file_location('package_device_build', Path(__file__).with_name('package_device_build.py'))
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)
apply_spec = importlib.util.spec_from_file_location('apply_device_build', Path(__file__).with_name('apply_device_build.py'))
applier = importlib.util.module_from_spec(apply_spec)
apply_spec.loader.exec_module(applier)
SOURCE = 'a' * 40


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


@pytest.fixture
def artifact(device_tree, tmp_path):
  output = tmp_path / 'artifact'
  packager.package(output)
  (output / 'source-commit.txt').write_text(SOURCE + '\n')
  return output


def test_apply_preserves_source_and_lists_only_binary_paths(artifact, tmp_path):
  target = tmp_path / 'checkout'
  target.mkdir()
  (target / 'source.py').write_text('unchanged')
  paths = applier.apply(artifact, SOURCE, target)
  assert set(paths) == set(packager.REQUIRED_BINARIES) | {
    'prebuilt', 'panda/board/obj/panda.bin.signed', 'panda/board/obj/bootstub.panda.bin',
  }
  assert (target / 'source.py').read_text() == 'unchanged'
  for name in packager.REQUIRED_BINARIES:
    packager.verify_binary(target / name)


@pytest.mark.parametrize('mismatch', ['source', 'checksum'])
def test_apply_rejects_mismatches_before_writing(artifact, tmp_path, mismatch):
  if mismatch == 'source':
    (artifact / 'source-commit.txt').write_text('b' * 40)
  else:
    (artifact / 'SHA256SUMS').write_text('invalid')
  target = tmp_path / 'checkout'
  with pytest.raises(ValueError):
    applier.apply(artifact, SOURCE, target)
  assert not target.exists()


@pytest.mark.parametrize('bad_entry', ['../escape', 'source.py', 'duplicate', 'symlink', 'missing', 'wrong_arch', 'bootstub'])
def test_apply_rejects_invalid_overlay_before_writing(artifact, tmp_path, bad_entry):
  archive = artifact / 'starpilot-device.tar.gz'
  with tarfile.open(archive) as bundle:
    entries = [(member, bundle.extractfile(member).read()) for member in bundle]
  if bad_entry == 'missing':
    entries = entries[1:]
  elif bad_entry == 'bootstub':
    entries = [(member, content) for member, content in entries if 'bootstub.' not in member.name]
  elif bad_entry == 'wrong_arch':
    for member, _ in entries:
      if member.name in packager.REQUIRED_BINARIES:
        entries = [(item, b'x' * len(content) if item is member else content) for item, content in entries]
        break
  elif bad_entry == 'duplicate':
    entries.append(entries[0])
  else:
    member = tarfile.TarInfo('prebuilt' if bad_entry == 'symlink' else bad_entry)
    if bad_entry == 'symlink':
      entries = [(item, content) for item, content in entries if item.name != 'prebuilt']
      member.type = tarfile.SYMTYPE
      member.linkname = '/outside'
    entries.append((member, b''))
  with tarfile.open(archive, 'w:gz') as bundle:
    for member, content in entries:
      bundle.addfile(member, io.BytesIO(content))
  digest = hashlib.sha256(archive.read_bytes()).hexdigest()
  (artifact / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n')
  target = tmp_path / 'checkout'
  with pytest.raises(ValueError):
    applier.apply(artifact, SOURCE, target)
  assert not target.exists()
