#!/usr/bin/env python3
"""Reuse accepted upstream QCOM captures only when every compiler input still matches."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from openpilot.common import prebuilt_manifest
from openpilot.common.file_chunker import materialize_file_chunked
from tools.laptop_device_build import qcom_artifact_contract as qcom


def prepare(root: Path, base: str) -> Path:
  root = root.resolve()
  def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
  revision = git('rev-parse', '--verify', '--end-of-options', base + '^{commit}')
  if not prebuilt_manifest.valid_receipt(git, revision):
    raise ValueError('Accepted upstream source has no matching protected build receipt')
  output = root / '.cache/qcom-artifact-package'
  expected = qcom.commands(root)
  if output.exists():
    for name, command in expected.items():
      qcom.verify(output, root, name, command)
    return output
  output.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='qcom-upstream-', dir=output.parent) as temporary:
    temporary = Path(temporary)
    source = temporary / 'source'
    source.mkdir()
    tracked = git('ls-tree', '-r', '--name-only', revision, '--', str(qcom.MODEL_DIR)).splitlines()
    artifacts_paths = [path for path in tracked if any(
      path == str(qcom.MODEL_DIR / name) or path.startswith(str(qcom.MODEL_DIR / name) + '.chunk') for name in expected)]
    paths = [*qcom.STATIC_SOURCES, 'tinygrad_model_runtime/legacy9d', *artifacts_paths]
    archive = temporary / 'source.tar'
    with archive.open('wb') as stream:
      subprocess.run(['git', '-C', str(root), 'archive', '--format=tar', revision, '--', *paths], stdout=stream, check=True)
    with tarfile.open(archive) as bundle:
      bundle.extractall(source, filter='data')
    if qcom.source_inventory(source) != qcom.source_inventory(root):
      raise ValueError('QCOM compiler inputs changed; obtain a newly compiled and verified hardware package')
    for name in expected:
      path = source / qcom.MODEL_DIR / name
      if not path.is_file():
        shutil.copyfile(materialize_file_chunked(path), path)
    manifest = qcom.record(source, source / qcom.MODEL_DIR)
    package = temporary / 'package'
    artifacts = package / 'artifacts'
    artifacts.mkdir(parents=True)
    for name in expected:
      shutil.copyfile(source / qcom.MODEL_DIR / name, artifacts / name)
    (package / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
    (package / 'upstream-commit.txt').write_text(revision + '\n')
    for name, command in expected.items():
      qcom.verify(package, root, name, command)
    package.rename(output)
  print(f'Prepared {len(expected)} source-matched QCOM captures from {revision}')
  return output


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--base', required=True)
  parser.add_argument('--root', type=Path, default=Path.cwd())
  args = parser.parse_args()
  prepare(args.root, args.base)
