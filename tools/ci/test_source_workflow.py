import json
from pathlib import Path
import subprocess

import pytest

import publish_device_release as release
import update_upstream as updates


@pytest.fixture
def repository(tmp_path, monkeypatch):
  origin = tmp_path / 'origin.git'
  subprocess.run(['git', 'init', '--bare', str(origin)], check=True, capture_output=True)
  work = tmp_path / 'work'
  work.mkdir()
  monkeypatch.chdir(work)
  release.git('init')
  release.git('config', 'user.name', 'CI test')
  release.git('config', 'user.email', 'test@example.invalid')
  release.git('config', 'commit.gpgsign', 'false')
  release.git('config', 'tag.gpgsign', 'false')
  release.git('remote', 'add', 'origin', str(origin))
  Path('source.txt').write_text('upstream\n')
  Path('removed.txt').write_text('old upstream file\n')
  Path('runtime').write_text('old binary\n')
  release.git('add', '.')
  release.git('commit', '-m', 'upstream')
  base = release.git('rev-parse', 'HEAD')
  release.git('push', 'origin', 'HEAD:refs/heads/StarPilot', 'HEAD:refs/heads/upstream-base')
  release.git('switch', '-c', 'custom')
  Path('feature.txt').write_text('custom feature\n')
  release.git('add', '.')
  release.git('commit', '-m', 'custom feature')
  custom = release.git('rev-parse', 'HEAD')
  release.git('push', 'origin', 'HEAD:refs/heads/custom')

  def overlay(artifact, source, root):
    assert source == release.git('rev-parse', 'HEAD')
    (root / 'runtime').write_text('tested binary\n')
    (root / 'prebuilt').touch()
    return ['runtime', 'prebuilt']

  monkeypatch.setattr(release, 'apply', overlay)
  return dict(base=base, custom=custom, origin=origin, work=work, tmp=tmp_path)


def advance_upstream(repo):
  release.git('switch', '--detach', repo['base'])
  Path('upstream.txt').write_text('upstream update\n')
  Path('removed.txt').unlink()
  release.git('add', '-A')
  release.git('commit', '-m', 'upstream update')
  return release.git('rev-parse', 'HEAD')


def candidate(repo):
  upstream = advance_upstream(repo)
  release.git('switch', '-c', 'updates/upstream-123-1', repo['custom'])
  release.git('rebase', '--onto', upstream, repo['base'])
  tested = release.git('rev-parse', 'HEAD')
  release.git('push', 'origin', 'HEAD:refs/heads/updates/upstream-123-1')
  return dict(base=repo['custom'], upstream_base=repo['base'], upstream=upstream,
              candidate=tested, branch='updates/upstream-123-1', run_id='123')


def test_release_after_rebase_is_append_only_and_exact_source_snapshot(repository):
  repo = repository
  metadata = candidate(repo)
  updates.accept(metadata, '123', repo['custom'])
  published = release.publish(repo['tmp'], metadata['candidate'])
  assert release.git('rev-parse', f'{published}^') == repo['base']
  assert release.git('show', f'{published}:runtime') == 'tested binary'
  assert release.git('show', f'{published}:feature.txt') == 'custom feature'
  assert release.git('show', f'{published}:upstream.txt') == 'upstream update'
  assert not release.git('ls-tree', published, 'removed.txt')
  assert set(release.git('diff', '--name-only', metadata['candidate'], published).splitlines()) == {'runtime', 'prebuilt'}
  assert release.remote_head('refs/heads/custom') == metadata['candidate']
  assert release.remote_head('refs/heads/StarPilot') == published
  assert release.git('rev-parse', f'device-{metadata["candidate"]}^{{}}') == published
  assert f'Source-Commit: {metadata["candidate"]}' in release.git('show', '-s', '--format=%B', published)


def test_stale_source_cannot_publish(repository):
  release.git('switch', '--detach', repository['base'])
  assert release.publish(repository['tmp'], repository['base']) is None
  assert release.remote_head('refs/heads/StarPilot') == repository['base']


def test_duplicate_publish_is_idempotent(repository):
  source = repository['custom']
  first = release.publish(repository['tmp'], source)
  release.git('reset', '--hard', source)
  assert release.publish(repository['tmp'], source) == first
  assert release.remote_head('refs/heads/StarPilot') == first


def test_source_advancing_during_overlay_does_not_publish(repository, monkeypatch):
  actual_apply = release.apply

  def concurrent_change(*args):
    paths = actual_apply(*args)
    release.git('push', '--force-with-lease', 'origin', f'{repository["base"]}:refs/heads/custom')
    return paths

  monkeypatch.setattr(release, 'apply', concurrent_change)
  assert release.publish(repository['tmp'], repository['custom']) is None
  assert release.remote_head('refs/heads/StarPilot') == repository['base']


def test_concurrent_install_update_rejects_release_and_tag_atomically(repository, monkeypatch):
  actual_git = release.git
  competing = actual_git('commit-tree', actual_git('rev-parse', 'HEAD^{tree}'), '-p', repository['base'], '-m', 'other release')

  def race(*args):
    if args[:2] == ('push', '--atomic'):
      actual_git('push', 'origin', f'{competing}:refs/heads/StarPilot')
    return actual_git(*args)

  monkeypatch.setattr(release, 'git', race)
  with pytest.raises(subprocess.CalledProcessError):
    release.publish(repository['tmp'], repository['custom'])
  assert release.remote_head('refs/heads/StarPilot') == competing
  assert not actual_git('ls-remote', 'origin', f'refs/tags/device-{repository["custom"]}')


def test_accept_preserves_backup_and_supports_publication_retry(repository):
  metadata = candidate(repository)
  for _ in range(2):
    assert updates.accept(metadata, '123', repository['custom']) == metadata['candidate']
  assert release.remote_head('refs/tags/source-backup/123') == repository['custom']
  assert release.remote_head('refs/heads/upstream-base') == metadata['upstream']
  assert release.remote_head('refs/heads/StarPilot') == repository['base']


@pytest.mark.parametrize('change', ['source', 'baseline', 'candidate', 'metadata'])
def test_accept_rejects_stale_or_mismatched_candidates(repository, change):
  metadata = candidate(repository)
  if change == 'metadata':
    metadata['run_id'] = '456'
  else:
    branch = {'source': 'custom', 'baseline': 'upstream-base', 'candidate': metadata['branch']}[change]
    replacement = metadata['upstream'] if change == 'baseline' else repository['base']
    release.git('push', '--force', 'origin', f'{replacement}:refs/heads/{branch}')
  before = release.remote_head('refs/heads/custom')
  with pytest.raises(ValueError):
    updates.accept(metadata, '123', repository['custom'])
  assert release.remote_head('refs/heads/custom') == before
  assert release.remote_head('refs/heads/StarPilot') == repository['base']


def redirect_upstream(monkeypatch, upstream):
  actual_git = updates.git

  def redirected(*args):
    if args[:3] == ('fetch', '--no-tags', 'https://github.com/firestar5683/StarPilot.git'):
      return actual_git('fetch', '--no-tags', 'origin', upstream)
    return actual_git(*args)

  monkeypatch.setattr(updates, 'git', redirected)


def test_prepare_rebases_only_custom_commits_without_promoting(repository, monkeypatch):
  upstream = advance_upstream(repository)
  release.git('push', 'origin', 'HEAD:refs/heads/test-upstream')
  release.git('switch', 'custom')
  redirect_upstream(monkeypatch, 'refs/heads/test-upstream')
  folder = repository['tmp'] / 'review'
  metadata = updates.prepare('123', '1', folder)
  assert metadata['upstream'] == upstream
  assert release.git('rev-list', '--count', f'{upstream}..{metadata["candidate"]}') == '1'
  assert json.loads((folder / 'candidate.json').read_text()) == metadata
  assert release.remote_head('refs/heads/custom') == repository['custom']
  assert release.remote_head('refs/heads/StarPilot') == repository['base']
  assert release.remote_head('refs/heads/upstream-base') == repository['base']


def test_prepare_current_upstream_is_noop(repository, monkeypatch):
  redirect_upstream(monkeypatch, 'refs/heads/upstream-base')
  folder = repository['tmp'] / 'review'
  assert updates.prepare('123', '1', folder) == {}
  assert 'No update needed' in (folder / 'review.txt').read_text()
  assert not release.git('ls-remote', 'origin', 'refs/heads/updates/*')


def test_prepare_conflict_leaves_source_and_install_unchanged(repository, monkeypatch):
  Path('source.txt').write_text('custom conflicting edit\n')
  release.git('add', 'source.txt')
  release.git('commit', '-m', 'custom change')
  source = release.git('rev-parse', 'HEAD')
  release.git('push', 'origin', 'HEAD:refs/heads/custom')
  release.git('switch', '--detach', repository['base'])
  Path('source.txt').write_text('upstream conflicting edit\n')
  release.git('add', 'source.txt')
  release.git('commit', '-m', 'upstream change')
  release.git('push', 'origin', 'HEAD:refs/heads/test-upstream')
  release.git('switch', 'custom')
  redirect_upstream(monkeypatch, 'refs/heads/test-upstream')
  folder = repository['tmp'] / 'review'
  with pytest.raises(subprocess.CalledProcessError):
    updates.prepare('123', '1', folder)
  assert 'source.txt' in (folder / 'review.txt').read_text()
  assert release.remote_head('refs/heads/custom') == source
  assert release.remote_head('refs/heads/StarPilot') == repository['base']
  assert not release.git('ls-remote', 'origin', 'refs/heads/updates/*')
