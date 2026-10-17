#!/usr/bin/env python3
"""Append a verified source + binary snapshot to the device install branch."""

import argparse
from pathlib import Path
import re
import subprocess

from apply_device_build import apply


def git(*args: str) -> str:
  return subprocess.check_output(['git', *args], text=True).strip()


def remote_head(ref: str) -> str:
  return git('ls-remote', '--exit-code', 'origin', ref).split()[0]


def publish(artifact: Path, source: str) -> str | None:
  if not re.fullmatch(r'[0-9a-f]{40}', source) or git('rev-parse', 'HEAD') != source:
    raise ValueError('Checkout must be the exact tested source revision')
  if remote_head('refs/heads/custom') != source:
    print('Source has been superseded; leaving the install branch unchanged.')
    return None
  git('diff', '--quiet')
  git('diff', '--cached', '--quiet')
  git('fetch', '--no-tags', 'origin', 'refs/heads/StarPilot')
  previous = git('rev-parse', 'FETCH_HEAD')
  tag = f'device-{source}'
  existing = git('ls-remote', 'origin', f'refs/tags/{tag}^{{}}')
  if existing:
    release = existing.split()[0]
    git('merge-base', '--is-ancestor', release, previous)
    print(f'Source already published: {release}')
    return release

  paths = apply(artifact, source, Path.cwd())
  git('add', '-f', '--', *paths)
  tree = git('write-tree')
  # The source tree may have been rebased. Only the previous install commit is
  # the parent, so device history always advances without merging old binaries.
  release = git('commit-tree', tree, '-p', previous, '-m',
                f'Release device build for {source} [skip ci]\n\nSource-Commit: {source}')
  if remote_head('refs/heads/custom') != source:
    print('Source advanced during publication; leaving the install branch unchanged.')
    return None
  git('tag', '-a', tag, release, '-m', f'Device release\n\nSource-Commit: {source}')
  # Both refs advance together. A concurrent install update rejects the normal
  # (never forced) push; a retry must re-read its parent first.
  git('push', '--atomic', 'origin', f'{release}:refs/heads/StarPilot', f'refs/tags/{tag}')
  print(f'Published {release} from {source}; tag {tag}')
  return release


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--artifact', type=Path, required=True)
  parser.add_argument('--source', required=True)
  args = parser.parse_args()
  publish(args.artifact, args.source)
