#!/usr/bin/env python3
"""Validate a CI binary overlay completely before applying it to its source tree."""

import argparse
import hashlib
from pathlib import Path
import re
import tarfile

from package_device_build import REQUIRED_BINARIES


def apply(artifact: Path, source: str, root: Path) -> list[str]:
  if not re.fullmatch(r'[0-9a-f]{40}', source):
    raise ValueError('Invalid source revision')
  if (artifact / 'source-commit.txt').read_text().strip() != source:
    raise ValueError('Artifact source does not match checkout')
  archive = artifact / 'starpilot-device.tar.gz'
  expected = (artifact / 'SHA256SUMS').read_text().strip()
  with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
  if expected != f'{actual}  {archive.name}':
    raise ValueError('Artifact checksum mismatch')

  required = set(REQUIRED_BINARIES) | {'prebuilt'}
  files = {}
  total_size = 0
  with tarfile.open(archive, 'r:gz') as bundle:
    for member in bundle:
      name = member.name
      firmware = re.fullmatch(r'panda/board/obj/(?:[a-z0-9_]+\.bin\.signed|bootstub\.[a-z0-9_]+\.bin)', name)
      if not member.isfile() or name in files or (name not in required and not firmware):
        raise ValueError(f'Unexpected archive entry: {name}')
      total_size += member.size
      if member.size < 0 or total_size > 64 * 1024 * 1024 or len(files) >= 256:
        raise ValueError('Binary overlay exceeds size limit')
      content = bundle.extractfile(member).read()
      if name in REQUIRED_BINARIES:
        if len(content) < 20 or content[:6] != b'\x7fELF\x02\x01' or int.from_bytes(content[18:20], 'little') != 183:
          raise ValueError(f'Invalid AArch64 binary: {name}')
      elif name != 'prebuilt' and not content:
        raise ValueError(f'Empty firmware: {name}')
      files[name] = content
  if not required.issubset(files):
    raise ValueError('Missing required runtime binaries')
  signed = [name for name in files if name.endswith('.bin.signed')]
  if not signed or any(f'panda/board/obj/bootstub.{Path(name).name.removesuffix(".signed")}' not in files for name in signed):
    raise ValueError('Missing panda firmware or matching bootstub')

  root = root.resolve()
  for name in files:
    destination = root / name
    if destination.is_symlink() or not destination.resolve().is_relative_to(root):
      raise ValueError(f'Unsafe destination: {name}')
  for name, content in files.items():
    destination = root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o755 if name in REQUIRED_BINARIES else 0o644)
  return sorted(files)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--artifact', type=Path, required=True)
  parser.add_argument('--source', required=True)
  parser.add_argument('--paths', type=Path, required=True)
  args = parser.parse_args()
  paths = apply(args.artifact, args.source, Path.cwd())
  args.paths.write_bytes(b'\0'.join(path.encode() for path in paths) + b'\0')
  print(f'Applied {len(paths)} verified binary files for {args.source}')
