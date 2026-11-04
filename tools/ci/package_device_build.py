#!/usr/bin/env python3
"""Package only comma-device runtime binaries and firmware, never the source tree."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_BINARIES = (
  'openpilot/common/libparams_c.so',
  'msgq_repo/msgq/_ipc.so',
  'msgq_repo/msgq/visionipc/_visionipc.so',
  'opendbc_repo/opendbc/can/_raw_value.so',
  'rednose_repo/rednose/helpers/ekf_sym_pyx.so',
  'openpilot/selfdrive/pandad/pandad',
  'openpilot/starpilot/flm/_primitive_walk.so',
  'openpilot/starpilot/ui/native/_raylib_extra.so',
  'openpilot/selfdrive/locationd/models/generated/libcar.so',
  'openpilot/selfdrive/locationd/models/generated/libpose.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados_ocp_solver_long.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libblasfeo.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libhpipm.so',
  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libqpOASES_e.so.3.1',
  'openpilot/system/loggerd/loggerd',
  'openpilot/system/loggerd/encoderd',
  'openpilot/system/loggerd/bootlog',
  'openpilot/tools/replay/replay',
  'openpilot/tools/replay/libmedia_mux.so',
)



REQUIRED_METADATA = ('openpilot/starpilot/flm/_primitive_walk.binding.json',)


def verify_metadata(content: bytes, native: bytes) -> None:
  if len(content) > 16 * 1024:
    raise ValueError('Native binding metadata exceeds size limit')
  value = json.loads(content)
  if (not isinstance(value, dict) or value.get('schema') != 1 or
      value.get('artifact_sha256') != hashlib.sha256(native).hexdigest()):
    raise ValueError('Native binding metadata does not match binary')


def verify_binary(path: Path) -> None:
  if path.is_symlink():
    raise ValueError(f'Expected a regular binary file: {path}')
  with path.open('rb') as binary:
    header = binary.read(20)
  # ELF64, little endian, EM_AARCH64. Reject host binaries and empty outputs.
  if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
    raise ValueError(f'Expected an AArch64 ELF binary: {path}')


def package(output: Path) -> Path:
  for relative in REQUIRED_BINARIES:
    verify_binary(ROOT / relative)
  verify_metadata((ROOT / REQUIRED_METADATA[0]).read_bytes(),
                  (ROOT / 'openpilot/starpilot/flm/_primitive_walk.so').read_bytes())
  if not (ROOT / 'prebuilt').is_file():
    raise ValueError('Device build did not produce the prebuilt marker')
  firmware_dir = ROOT / 'panda/board/obj'
  signed = sorted(firmware_dir.glob('*.bin.signed'))
  if not signed:
    raise ValueError('Missing signed panda firmware')
  firmware = signed + [firmware_dir / f'bootstub.{path.name.removesuffix(".signed")}' for path in signed]
  if any(path.is_symlink() or not path.is_file() or path.stat().st_size == 0 for path in firmware):
    raise ValueError('Missing or empty panda firmware')

  # This allowlist deliberately excludes sources, Git metadata, caches and secrets.
  paths = set(REQUIRED_BINARIES) | set(REQUIRED_METADATA) | {'prebuilt'}
  paths.update(path.relative_to(ROOT).as_posix() for path in firmware)
  output.mkdir(parents=True, exist_ok=True)
  archive = output / 'starpilot-device.tar.gz'
  with tarfile.open(archive, 'w:gz', compresslevel=1, dereference=False) as bundle:
    for relative in sorted(paths):
      bundle.add(ROOT / relative, arcname=relative, recursive=False)
  with archive.open('rb') as bundle:
    digest = hashlib.file_digest(bundle, 'sha256').hexdigest()
  (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n')
  print(f'Packaged {len(paths)} binary files: {archive}')
  return archive


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  package(parser.parse_args().output)
