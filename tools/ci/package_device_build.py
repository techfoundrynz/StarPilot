#!/usr/bin/env python3
"""Package only comma-device runtime binaries and firmware, never the source tree."""

import argparse
import hashlib
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_BINARIES = (
  'common/params_pyx.so',
  'common/transformations/transformations.so',
  'cereal/messaging/bridge',
  'msgq_repo/msgq/ipc_pyx.so',
  'msgq_repo/msgq/visionipc/visionipc_pyx.so',
  'rednose_repo/rednose/helpers/ekf_sym_pyx.so',
  'selfdrive/pandad/pandad',
  'selfdrive/pandad/pandad_api_impl.so',
  'selfdrive/modeld/models/commonmodel_pyx.so',
  'selfdrive/locationd/models/generated/libcar.so',
  'selfdrive/locationd/models/generated/libpose.so',
  'selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so',
  'selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/libacados_ocp_solver_lat.so',
  'selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so',
  'selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados_ocp_solver_long.so',
  'system/camerad/camerad',
  'system/loggerd/loggerd',
  'system/loggerd/encoderd',
  'system/loggerd/bootlog',
)


def verify_binary(path: Path) -> None:
  with path.open('rb') as binary:
    header = binary.read(20)
  # ELF64, little endian, EM_AARCH64. Reject host binaries and empty outputs.
  if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
    raise ValueError(f'Expected an AArch64 ELF binary: {path}')


def package(output: Path) -> Path:
  for relative in REQUIRED_BINARIES:
    verify_binary(ROOT / relative)
  if not (ROOT / 'prebuilt').is_file():
    raise ValueError('Device build did not produce the prebuilt marker')
  firmware = sorted((ROOT / 'panda/board/obj').glob('*.bin.signed'))
  firmware += sorted((ROOT / 'panda/board/obj').glob('bootstub.*.bin'))
  if not firmware or any(path.stat().st_size == 0 for path in firmware):
    raise ValueError('Missing or empty panda firmware')

  # This allowlist deliberately excludes sources, Git metadata, caches and secrets.
  paths = set(REQUIRED_BINARIES) | {'prebuilt'}
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
