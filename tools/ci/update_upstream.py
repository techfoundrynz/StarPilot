#!/usr/bin/env python3
"""Prepare a reviewable upstream rebase, or accept an already tested candidate."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from publish_device_release import git, remote_head


def output(**values: str) -> None:
  if path := os.environ.get('GITHUB_OUTPUT'):
    with open(path, 'a') as stream:
      for key, value in values.items():
        stream.write(f'{key}={value}\n')


def prepare(run_id: str, attempt: str, directory: Path) -> dict:
  if not run_id.isdecimal() or not attempt.isdecimal():
    raise ValueError('Invalid workflow run identifier')
  directory.mkdir(parents=True, exist_ok=True)
  base = git('rev-parse', 'HEAD')
  if remote_head('refs/heads/custom') != base:
    raise ValueError('custom changed since this workflow started; run it again')
  git('fetch', '--no-tags', 'origin', 'refs/heads/upstream-base')
  old_upstream = git('rev-parse', 'FETCH_HEAD')
  git('fetch', '--no-tags', 'https://github.com/firestar5683/StarPilot.git', 'refs/heads/StarPilot')
  upstream = git('rev-parse', 'FETCH_HEAD')
  git('merge-base', '--is-ancestor', old_upstream, base)
  branch = f'updates/upstream-{run_id}-{attempt}'
  if upstream == old_upstream:
    output(changed='false')
    (directory / 'review.txt').write_text('Already based on the latest upstream StarPilot commit. No update needed.\n')
    return {}
  git('switch', '-c', branch)
  git('config', 'rerere.enabled', 'true')
  git('config', 'rerere.autoupdate', 'false')
  try:
    git('rebase', '--onto', upstream, old_upstream)
  except subprocess.CalledProcessError:
    conflicts = git('diff', '--name-only', '--diff-filter=U')
    (directory / 'review.txt').write_text(
      f'Rebase stopped. custom and StarPilot were not changed.\n\nConflicting paths:\n{conflicts}\n\n'
      + 'Reproduce locally on a temporary branch:\n'
      + f'git rebase --onto {upstream} {old_upstream}\n', encoding='utf-8')
    git('rebase', '--abort')
    raise
  candidate = git('rev-parse', 'HEAD')
  metadata = dict(base=base, upstream_base=old_upstream, upstream=upstream,
                  candidate=candidate, branch=branch, run_id=run_id)
  (directory / 'candidate.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
  review = git('range-diff', f'{old_upstream}..{base}', f'{upstream}..{candidate}')
  (directory / 'review.txt').write_text(review + '\n', encoding='utf-8')
  git('push', f'--force-with-lease=refs/heads/{branch}:', 'origin', f'HEAD:refs/heads/{branch}')
  output(changed='true', source=candidate, branch=branch)
  return metadata


def accept(metadata: dict, run_id: str, workflow_sha: str) -> str:
  for key in ('base', 'upstream_base', 'upstream', 'candidate'):
    if not re.fullmatch(r'[0-9a-f]{40}', metadata[key]):
      raise ValueError(f'Invalid {key} revision')
  branch = metadata['branch']
  if (metadata['run_id'] != run_id or metadata['base'] != workflow_sha or
      not re.fullmatch(rf'updates/upstream-{re.escape(run_id)}-[0-9]+', branch)):
    raise ValueError('Candidate metadata does not match the successful preparation run')
  for ref in ('custom', 'upstream-base', branch):
    git('fetch', '--no-tags', 'origin', f'+refs/heads/{ref}:refs/remotes/origin/{ref}')
  candidate = metadata['candidate']
  if git('rev-parse', f'refs/remotes/origin/{branch}') != candidate:
    raise ValueError('Candidate branch changed after testing; prepare a new candidate')
  git('merge-base', '--is-ancestor', metadata['upstream'], candidate)
  current = git('rev-parse', 'refs/remotes/origin/custom')
  current_upstream = git('rev-parse', 'refs/remotes/origin/upstream-base')
  if (current, current_upstream) == (candidate, metadata['upstream']):
    output(source=candidate)
    return candidate  # Retry publication after an already successful promotion.
  if (current, current_upstream) != (metadata['base'], metadata['upstream_base']):
    raise ValueError('Source or upstream baseline advanced; prepare a new candidate')
  # The explicit leases protect both development refs; install history is never
  # rewritten. Preserve the previous patch stack in the same atomic transaction.
  git('push', '--atomic', f'--force-with-lease=refs/heads/custom:{current}',
      f'--force-with-lease=refs/heads/upstream-base:{current_upstream}', 'origin',
      f'{candidate}:refs/heads/custom', f'{metadata["upstream"]}:refs/heads/upstream-base',
      f'{current}:refs/tags/source-backup/{run_id}')
  output(source=candidate)
  return candidate


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  subparsers = parser.add_subparsers(dest='command', required=True)
  preparation = subparsers.add_parser('prepare')
  preparation.add_argument('--run-id', required=True)
  preparation.add_argument('--attempt', required=True)
  preparation.add_argument('--directory', type=Path, required=True)
  acceptance = subparsers.add_parser('accept')
  acceptance.add_argument('--metadata', type=Path, required=True)
  acceptance.add_argument('--run-id', required=True)
  acceptance.add_argument('--workflow-sha', required=True)
  args = parser.parse_args()
  if args.command == 'prepare':
    prepare(args.run_id, args.attempt, args.directory)
  else:
    accept(json.loads(args.metadata.read_text()), args.run_id, args.workflow_sha)
