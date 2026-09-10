import fcntl
import json
from pathlib import Path

import pytest

from app import update_cleanup as cleanup


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def env(path):
    path.mkdir(parents=True)
    (path / 'pyvenv.cfg').write_text('home = /usr/bin')
    (path / 'packages').write_bytes(b'installed dependencies')
    return path


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '0')
    repo = tmp_path / 'repo'
    data = repo / 'data'
    releases = data / 'releases'
    environments = [env(releases / f'v2.0.{n}-{100+n}' / 'venv') for n in range(1, 7)]
    active, previous = environments[-1], environments[-2]
    (repo / '.venv').symlink_to(active)
    backups = data / 'backups'
    snapshots = []
    for n in range(1, 7):
        backup = backups / f'before-update-{100+n}'
        backup.mkdir(parents=True)
        (backup / 'bot.db').write_text('snapshot')
        snapshots.append(backup)
    plan = dict(revision='new-revision', old_revision='old-revision', tag='v2.0.6',
                environment=str(active), old_environment=str(previous), release=str(active.parent),
                activated=True, backup=str(snapshots[-1].relative_to(repo)))
    save(data / 'update-plan.json', plan)
    save(data / 'update-status.json', dict(phase='complete', tag='v2.0.6'))
    lock = tmp_path / 'update.lock'
    lock.touch()
    for name in ['bot.db', 'autoposter/autoposter.db', 'autoposter/media/image.jpg', 'autoposter.env']:
        file = data / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('keep live data')
    (repo / '.env').write_text('keep credentials')
    return dict(repo=repo, data=data, releases=releases, backups=backups, active=active,
                previous=previous, plan=plan, lock=lock, envs=environments, snapshots=snapshots)


def run(i):
    return cleanup.cleanup(i['repo'], 'new-revision', i['active'], lock_path=i['lock'])


def test_success_retains_two_environments_three_snapshots_and_all_live_data(installation):
    i = installation
    before = {file: file.read_bytes() for file in [i['repo'] / '.env', i['data'] / 'bot.db',
              i['data'] / 'autoposter/autoposter.db', i['data'] / 'autoposter/media/image.jpg',
              i['data'] / 'autoposter.env']}
    result = run(i)
    assert result['errors'] == [] and len(result['removed']) == 7
    assert sorted(i['releases'].iterdir()) == [i['previous'].parent, i['active'].parent]
    assert sorted(i['backups'].iterdir()) == i['snapshots'][-3:]
    assert (i['repo'] / '.venv').resolve() == i['active']
    assert {file: file.read_bytes() for file in before} == before
    assert run(i) == {'skipped': 'already-cleaned'}


@pytest.mark.parametrize('phase', ['preparing', 'testing', 'switching', 'checking', 'failed', 'rolled_back', 'current'])
def test_only_completed_updates_are_cleaned(installation, phase):
    i = installation
    save(i['data'] / 'update-status.json', dict(phase=phase, tag='v2.0.6'))
    assert run(i)['skipped'] == 'update-not-complete'
    assert all(path.exists() for path in i['envs'] + i['snapshots'])


def test_lock_excludes_running_updater_even_after_success_report(installation):
    i = installation
    with i['lock'].open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(i)['skipped'] == 'update-running'
        assert i['envs'][0].exists()
    assert len(run(i)['removed']) == 7


@pytest.mark.parametrize('case', ['preview', 'pending', 'revision', 'environment', 'missing-plan', 'corrupt-plan', 'missing-previous', 'missing-lock'])
def test_uncertain_installations_are_untouched(installation, monkeypatch, case):
    i = installation
    if case == 'preview': monkeypatch.setenv('MP_PREVIEW', '1')
    elif case == 'pending': (i['data'] / 'deployment-pending').touch()
    elif case == 'revision':
        i['plan']['revision'] = 'different'
        save(i['data'] / 'update-plan.json', i['plan'])
    elif case == 'environment':
        (i['repo'] / '.venv').unlink()
        (i['repo'] / '.venv').symlink_to(i['previous'])
    elif case == 'missing-plan': (i['data'] / 'update-plan.json').unlink()
    elif case == 'corrupt-plan': (i['data'] / 'update-plan.json').write_text('{')
    elif case == 'missing-previous': (i['previous'] / 'pyvenv.cfg').unlink()
    elif case == 'missing-lock': i['lock'].unlink()
    assert 'skipped' in run(i)
    assert all(path.exists() for path in i['envs'] + i['snapshots'])


def test_first_migration_protects_original_environment_then_removes_it_when_obsolete(installation):
    i = installation
    legacy = env(i['active'].parent / 'previous-venv')
    i['plan']['old_environment'] = str(legacy)
    save(i['data'] / 'update-plan.json', i['plan'])
    run(i)
    assert legacy.exists() and i['active'].exists()
    next_env = env(i['releases'] / 'v2.0.7-107' / 'venv')
    i['plan'].update(environment=str(next_env), old_environment=str(i['active']), release=str(next_env.parent))
    save(i['data'] / 'update-plan.json', i['plan'])
    (i['repo'] / '.venv').unlink()
    (i['repo'] / '.venv').symlink_to(next_env)
    i['active'] = next_env
    run(i)
    assert not legacy.exists() and next_env.exists()


def test_cleanup_preserves_unknown_folders_newer_attempts_and_symlink_targets(installation, tmp_path):
    i = installation
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'important').write_text('keep')
    (i['releases'] / 'v2.0.0-1').symlink_to(outside)
    (i['backups'] / 'before-update-1').symlink_to(outside)
    unknown = i['releases'] / 'manual-backup'
    unknown.mkdir()
    future = env(i['releases'] / 'v2.0.7-999' / 'venv')
    # A link inside a removable old release must not be followed.
    (i['envs'][0] / 'outside-link').symlink_to(outside)
    run(i)
    assert (outside / 'important').read_text() == 'keep'
    assert unknown.exists() and future.exists()
    assert (i['releases'] / 'v2.0.0-1').is_symlink()


def test_redirected_storage_root_is_not_cleaned(installation, tmp_path):
    i = installation
    moved = tmp_path / 'moved-backups'
    i['backups'].rename(moved)
    i['backups'].symlink_to(moved)
    assert run(i)['skipped'] == 'unknown-storage-layout'
    assert len(list(moved.iterdir())) == 6


def test_deletion_failure_is_reported_and_retried_without_touching_active(installation, monkeypatch):
    i = installation
    original = cleanup.shutil.rmtree
    def remove(path):
        if path == i['envs'][0].parent: raise PermissionError('test denied')
        original(path)
    monkeypatch.setattr(cleanup.shutil, 'rmtree', remove)
    remove.avoids_symlink_attacks = True
    result = run(i)
    assert result['errors'] and i['active'].exists() and i['previous'].exists()
    monkeypatch.setattr(cleanup.shutil, 'rmtree', original)
    assert run(i)['errors'] == []
    assert not i['envs'][0].exists()
