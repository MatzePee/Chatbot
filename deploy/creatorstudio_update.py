#!/usr/bin/env python3
"""Transactional code/environment update. Only the supervisor controls systemd.

Preparation and all repository operations run as the service user. Current
messages, tokens and settings are NEVER restored automatically from an old backup.
"""
from pathlib import Path
import contextlib
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import venv

TAG = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
HEALTH_PORT = 8000
PRIVATE = re.compile(r'^(?:\.env(?!\.example$)(?:$|\.)|data/|\.venv(?:$|/|-)|exports/|\.askpass-)')


def command(args, cwd=None, timeout=1200):
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': 'true', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'})
    if result.returncode:
        detail = (result.stderr or result.stdout)[-1600:]
        detail = re.sub(r'https://[^/@\s]+@', 'https://***@', detail)
        raise RuntimeError(f'{Path(str(args[0])).name} failed: {detail}')
    return result.stdout.strip()


def git(repo, *args):
    return command(['git', '-C', repo, *args], timeout=180)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.update-')
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    os.replace(temporary, path)


def state(repo, phase, message, **extra):
    path = repo / 'data/update-status.json'
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    save(path, {**previous, 'phase': phase, 'message': message, 'updated_at': time.time(), **extra})


def plan(repo):
    return json.loads((repo / 'data/update-plan.json').read_text())


def newest_tag(tags):
    valid = [(tuple(map(int, m.groups())), tag) for tag in tags if (m := TAG.fullmatch(tag))]
    if not valid:
        raise RuntimeError('Keine veröffentlichte Version vX.Y.Z gefunden.')
    return max(valid)[1]


def extract_archive(archive, destination):
    with tarfile.open(archive) as tar:
        for item in tar.getmembers():
            if PRIVATE.match(item.name) or Path(item.name).is_absolute() or '..' in Path(item.name).parts:
                raise RuntimeError('Private oder unsichere Datei in der Version: ' + item.name)
            if not (item.isfile() or item.isdir()):
                raise RuntimeError('Verknüpfungen sind in Update-Archiven nicht erlaubt: ' + item.name)
        tar.extractall(destination, filter='data')


def snapshot(repo, destination):
    destination.mkdir(parents=True, mode=0o700)
    for rel in ('data/bot.db', 'data/autoposter/autoposter.db'):
        source = repo / rel
        if source.exists():
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Datensicherung konnte nicht geprüft werden.')
            target.chmod(0o600)
    for rel in ('.env', 'data/autoposter.env'):
        if (repo / rel).exists():
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / rel, target)
            target.chmod(0o600)


def prepare(repo):
    if sys.version_info < (3, 11) or not hasattr(tarfile, 'data_filter'):
        raise RuntimeError('Eine aktuelle Python-Version ab 3.11 mit tarfile.data_filter ist erforderlich.')
    state(repo, 'preparing', 'Neue Version und Voraussetzungen werden geprüft.', tag='', backup='')
    if git(repo, 'status', '--porcelain'):
        raise RuntimeError('Lokale Programmänderungen auf dem Server. Update abgebrochen; laufende Version bleibt erhalten.')
    git(repo, 'fetch', '--tags', '--prune', 'origin')
    # Only tags currently published by the configured origin count as releases.
    remote = git(repo, 'ls-remote', '--tags', '--refs', 'origin')
    tags = {line.split()[1].removeprefix('refs/tags/'): line.split()[0] for line in remote.splitlines() if len(line.split()) == 2}
    tag = newest_tag(tags)
    if git(repo, 'rev-parse', f'refs/tags/{tag}') != tags[tag]:
        raise RuntimeError('Lokales und veröffentlichtes Versions-Tag stimmen nicht überein.')
    revision = git(repo, 'rev-parse', f'{tag}^{{commit}}')
    old_revision = git(repo, 'rev-parse', 'HEAD')
    if revision == old_revision:
        state(repo, 'current', 'Die installierte Version ist bereits aktuell.', tag=tag)
        return False
    installed = git(repo, 'tag', '--points-at', 'HEAD').splitlines()
    stable = [t for t in installed if TAG.fullmatch(t)]
    if stable and tuple(map(int, TAG.fullmatch(tag).groups())) <= tuple(map(int, TAG.fullmatch(newest_tag(stable)).groups())):
        raise RuntimeError('Ein automatisches Downgrade ist nicht erlaubt.')
    if shutil.disk_usage(repo).free < 1_500_000_000:
        raise RuntimeError('Für Umgebung und Sicherung sind mindestens 1,5 GB frei erforderlich.')
    release = repo / 'data/releases' / (tag + '-' + str(time.time_ns()))
    release.mkdir(parents=True, mode=0o700)
    archive = release / 'code.tar'
    command(['git', '-C', repo, 'archive', '--format=tar', '--output', archive, revision])
    code = release / 'code'
    code.mkdir()
    extract_archive(archive, code)
    archive.unlink()
    if not (code / 'tools/release_smoke.py').exists():
        raise RuntimeError('Diese Version unterstützt die geprüfte CreatorStudio-Aktualisierung noch nicht.')
    state(repo, 'preparing', 'Abhängigkeiten werden getrennt vom laufenden Bot installiert.', tag=tag)
    environment = release / 'venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    command([environment / 'bin/python', '-m', 'pip', 'install', '-r', code / 'requirements.txt'])
    command([environment / 'bin/python', '-m', 'pip', 'check'])
    state(repo, 'testing', 'Serverstart wird mit Datenkopien und gesperrtem Netzwerk geprüft.', tag=tag)
    command([environment / 'bin/python', code / 'tools/release_smoke.py', '--source', repo], cwd=code, timeout=300)
    save(repo / 'data/update-plan.json', {'tag': tag, 'revision': revision, 'old_revision': old_revision,
         'release': str(release), 'environment': str(environment), 'old_environment': '', 'activated': False})
    return True


def activate(repo):
    data = plan(repo)
    if git(repo, 'rev-parse', 'HEAD') != data['old_revision'] or git(repo, 'status', '--porcelain'):
        raise RuntimeError('Programmcode wurde während der Vorbereitung geändert; Wechsel abgebrochen.')
    destination = repo / 'data/backups' / ('before-update-' + str(time.time_ns()))
    snapshot(repo, destination)
    data['backup'] = str(destination.relative_to(repo))
    save(repo / 'data/update-plan.json', data)
    state(repo, 'switching', 'Daten gesichert; Programm und Umgebung werden gewechselt.', backup=data['backup'])
    old = repo / '.venv'
    data['old_environment_was_symlink'] = old.is_symlink()
    if old.is_symlink():
        data['old_environment'] = str(old.resolve())
    else:
        data['old_environment'] = str(Path(data['release']) / 'previous-venv')
    save(repo / 'data/update-plan.json', data)
    # Record rollback targets before either operation can fail.
    if old.is_symlink():
        old.unlink()
    else:
        old.rename(data['old_environment'])
    old.symlink_to(data['environment'], target_is_directory=True)
    git(repo, 'checkout', '--detach', '--force', data['revision'])
    data['activated'] = True
    save(repo / 'data/update-plan.json', data)


def rollback(repo):
    data = plan(repo)
    git(repo, 'checkout', '--detach', '--force', data['old_revision'])
    previous = data.get('old_environment')
    if previous and Path(previous).is_dir():
        current = repo / '.venv'
        if current.is_symlink():
            current.unlink()
        if not current.exists():
            if data.get('old_environment_was_symlink', True):
                current.symlink_to(previous, target_is_directory=True)
            else:
                Path(previous).rename(current)
    # Keep the freshest messages and rotating OAuth tokens, including writes
    # after activation. Schema changes were checked on copies before the switch.
    (repo / 'data/deployment-pending').unlink(missing_ok=True)


def healthy(expected, pending, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{HEALTH_PORT}/api/creatorpilot/health', timeout=3) as response:
                value = json.load(response)
            if (value.get('application') == 'MP CreatorStudio' and value.get('revision') == expected
                and value.get('preview') is False and value.get('deployment_pending') is pending
                and (pending or value.get('autochat_worker') is True)):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def supervise(repo, user, service):
    if os.geteuid() != 0:
        raise RuntimeError('Der Update-Supervisor benötigt root; Programmcode läuft weiterhin als Dienstbenutzer.')
    def child(action, *args):
        return command(['runuser', '-u', user, '--', sys.executable, Path(__file__).resolve(), action, repo, *args], timeout=1800)
    def report(phase, message):
        child('status', phase, message)
    stopped = False
    try:
        child('prepare')
        current = json.loads((repo / 'data/update-status.json').read_text())
        if current['phase'] == 'current':
            return
        # Maintenance marker suppresses jobs and external requests in the new
        # process until it has answered the first health check successfully.
        child('hold')
        stopped = True
        command(['systemctl', 'stop', service], timeout=120)
        child('activate')
        report('checking', 'Neue Version startet zunächst ohne Versand zur Funktionsprüfung.')
        command(['systemctl', 'start', service], timeout=120)
        data = plan(repo)
        if not healthy(data['revision'], True):
            raise RuntimeError('Funktionsprüfung der neuen Version fehlgeschlagen.')
        command(['systemctl', 'stop', service], timeout=120)
        child('release')
        command(['systemctl', 'start', service], timeout=120)
        if not healthy(data['revision'], False):
            raise RuntimeError('AutoChat ist nach dem Start nicht betriebsbereit.')
        report('complete', 'Update abgeschlossen. AutoChat läuft mit den bisherigen Einstellungen.')
    except Exception as exc:
        if stopped:
            try:
                command(['systemctl', 'stop', service], timeout=120)
                child('rollback')
                command(['systemctl', 'start', service], timeout=120)
            except Exception as rollback_error:
                report('failed', 'Update und automatischer Wiederanlauf fehlgeschlagen. Dienst muss geprüft werden. '
                       + str(exc)[-600:] + ' Wiederanlauf: ' + str(rollback_error)[-600:])
                raise
            # The previous app predates the richer CreatorStudio health route.
            restored = False
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{HEALTH_PORT}/health', timeout=3) as response:
                        restored = bool(json.load(response).get('ok'))
                    if restored:
                        break
                except Exception:
                    pass
                time.sleep(1)
            report('rolled_back' if restored else 'failed', 'Update fehlgeschlagen. ' + ('Bisherige Version wieder gestartet. ' if restored else 'Wiederanlauf muss geprüft werden. ') + str(exc)[-1200:])
        else:
            child('release')
            report('failed', 'Vorbereitung fehlgeschlagen; bisherige Version bleibt aktiv. ' + str(exc)[-1200:])
        raise


def main():
    action, root, *extra = sys.argv[1:]
    repo = Path(root).resolve()
    if action == 'run':
        with open('/run/lock/mp-creatorstudio-update.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            supervise(repo, *extra)
    elif action == 'prepare':
        prepare(repo)
    elif action == 'activate':
        activate(repo)
    elif action == 'rollback':
        rollback(repo)
    elif action == 'hold':
        (repo / 'data/deployment-pending').touch(mode=0o600)
    elif action == 'release':
        (repo / 'data/deployment-pending').unlink(missing_ok=True)
    elif action == 'status':
        state(repo, *extra)
    else:
        raise RuntimeError('Unbekannte Update-Aktion.')


if __name__ == '__main__':
    main()
