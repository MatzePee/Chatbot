"""Discover and repair an existing systemd installation without restarting it."""
import argparse
import ast
import fcntl
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

SOURCE = Path(__file__).resolve().parent
WRAPPER = Path('/usr/local/bin/fanvue-admin')
SUPERVISOR = Path('/usr/local/libexec/mp-creatorstudio-update')
CHECKER = Path('/usr/local/libexec/mp-creatorstudio-admin-permissions')
SUDOERS = Path('/etc/sudoers.d/fanvue-admin')
BACKUPS = Path('/var/backups/mp-creatorstudio')
PROPERTIES = ('Id', 'LoadState', 'User', 'WorkingDirectory', 'ExecStart',
              'MainPID', 'NoNewPrivileges', 'DynamicUser', 'Environment')


def run(args, timeout=30):
    result = subprocess.run([str(arg) for arg in args], capture_output=True,
                            text=True, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        # Never dump Environment/ExecStart: a unit could contain credentials.
        raise RuntimeError(f'{Path(str(args[0])).name}: Prüfung fehlgeschlagen (Status {result.returncode}).')
    return result.stdout.strip()


@dataclass(frozen=True)
class Installation:
    service: str
    user: str
    repo: Path
    port: int


def properties(text):
    return {key: value for line in text.splitlines() if '=' in line
            for key, value in [line.split('=', 1)]}


def service_arguments(info):
    pid = info.get('MainPID', '0')
    if pid.isdigit() and int(pid):
        try:
            return Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
        except OSError:
            pass
    match = re.search(r'argv\[\]=(.*?)\s*;\s*(?:ignore_errors|start_time)=', info.get('ExecStart', ''))
    return shlex.split(match[1]) if match else []


def option(arguments, name, default=None):
    for index, value in enumerate(arguments):
        if value.startswith(name + '='):
            return value.split('=', 1)[1]
        if value == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return default


def inspect_installation(info):
    args = service_arguments(info)
    if info.get('LoadState') != 'loaded' or 'app.main:app' not in args or not any('uvicorn' in arg for arg in args):
        return None
    repo = Path(info.get('WorkingDirectory') or '/')
    if not repo.is_absolute() or not (repo / 'data/bot.db').is_file() or not (repo / 'app/main.py').is_file():
        return None
    service = info.get('Id', '')
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.service', service):
        raise RuntimeError('Der erkannte Dienstname benötigt eine manuelle Prüfung.')
    raw_user = info.get('User') or 'root'
    account = pwd.getpwuid(int(raw_user)) if raw_user.isdigit() else pwd.getpwnam(raw_user)
    if account.pw_uid == 0 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', account.pw_name):
        raise RuntimeError(f'{service}: Ein fester Dienstbenutzer ohne root-Rechte ist erforderlich.')
    if info.get('DynamicUser') == 'yes' or info.get('NoNewPrivileges') == 'yes':
        raise RuntimeError(f'{service}: Die Diensteinstellungen verhindern die benötigte sudo-Ausführung. Es wurde nichts geändert.')
    if not os.access(repo / '.venv/bin/python', os.X_OK):
        raise RuntimeError(f'{service}: Die bestehende Python-Umgebung .venv wurde nicht gefunden.')
    if option(args, '--uds') or option(args, '--fd'):
        raise RuntimeError(f'{service}: Dieser Socket-Start benötigt eine angepasste Funktionsprüfung.')
    environment = dict(item.split('=', 1) for item in shlex.split(info.get('Environment', '')) if '=' in item)
    port = option(args, '--port', environment.get('UVICORN_PORT', '8000'))
    if not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError(f'{service}: Der HTTP-Port konnte nicht eindeutig erkannt werden.')
    host = option(args, '--host', environment.get('UVICORN_HOST', '127.0.0.1'))
    if host not in ('0.0.0.0', '127.0.0.1', 'localhost'):
        raise RuntimeError(f'{service}: Die Funktionsprüfung benötigt einen über 127.0.0.1 erreichbaren Dienst.')
    return Installation(service, account.pw_name, repo.resolve(), int(port))


def discover(service=None):
    if service:
        if not re.fullmatch(r'[A-Za-z0-9_.@:-]+', service):
            raise RuntimeError('Ungültiger Dienstname.')
        units = [service if service.endswith('.service') else service + '.service']
    else:
        listing = run(['systemctl', 'list-unit-files', '--type=service', '--no-legend', '--no-pager'])
        # Unit-file listings include non-instantiated templates (e.g. getty@),
        # which make `systemctl show` fail. Loaded units supply actual instances.
        loaded = run(['systemctl', 'list-units', '--all', '--type=service', '--plain', '--no-legend', '--no-pager'])
        units = sorted({line.split()[0] for line in (listing + '\n' + loaded).splitlines()
                        if line.split() and line.split()[0].endswith('.service')
                        and not line.split()[0].endswith('@.service')})
    if not units:
        raise RuntimeError('Kein systemd-Dienst gefunden.')
    output = run(['systemctl', 'show', '--no-pager', '--property=' + ','.join(PROPERTIES), *units])
    candidates = {}
    for block in output.split('\n\n'):
        installation = inspect_installation(properties(block))
        if installation:
            candidates[installation.service] = installation
    if not candidates:
        raise RuntimeError('Kein bestehender Fanvue-Chatbot mit app.main:app und data/bot.db gefunden. Es wurde nichts geändert.')
    if len(candidates) != 1:
        raise RuntimeError('Mehrere Installationen gefunden: ' + ', '.join(sorted(candidates))
                           + '. Erneut mit --service DIENSTNAME ausführen.')
    return next(iter(candidates.values()))


def validate_runtime(installation):
    if sys.version_info < (3, 11) or not hasattr(tarfile, 'data_filter'):
        raise RuntimeError('Eine aktuelle Python-Version ab 3.11 mit tarfile.data_filter ist erforderlich. Bitte zuerst Python aktualisieren lassen; der Bot bleibt unverändert.')
    for executable in ('systemctl', 'systemd-run', 'runuser', 'sudo', 'visudo', 'git', 'bash'):
        if not shutil.which(executable):
            raise RuntimeError(f'{executable} fehlt; es wurde nichts geändert.')
    run(['visudo', '-c', '-q'])
    # Exercise ensurepip/venv as the real service account, in disposable storage.
    temp = Path(run(['runuser', '-u', installation.user, '--', 'mktemp', '-d', '/tmp/mp-creatorstudio-python-XXXXXX']))
    try:
        run(['runuser', '-u', installation.user, '--', '/usr/bin/python3', '-m', 'venv', temp / 'venv'], timeout=120)
    except RuntimeError as exc:
        raise RuntimeError('Die separate Python-Umgebung konnte nicht erstellt werden. Bitte python3-venv/ensurepip prüfen lassen; der Bot bleibt unverändert.') from exc
    finally:
        shutil.rmtree(temp)
    run(['runuser', '-u', installation.user, '--', 'git', '-C', installation.repo, 'rev-parse', '--git-dir'])


def rendered_files(installation):
    wrapper = (SOURCE / 'fanvue-admin').read_text()
    for key, value in {'REPO': str(installation.repo), 'SVC_USER': installation.user, 'SERVICE': installation.service}.items():
        wrapper, count = re.subn(r'^' + key + r'=.*$', lambda _: key + '=' + shlex.quote(value), wrapper, count=1, flags=re.M)
        if count != 1:
            raise RuntimeError('Unvollständige Vorlage für den Update-Helfer.')
    supervisor = (SOURCE / 'creatorstudio_update.py').read_text()
    supervisor, count = re.subn(r'^HEALTH_PORT = \d+$', f'HEALTH_PORT = {installation.port}', supervisor, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError('Die Vorlage unterstützt die automatische Porterkennung nicht.')
    checker = (SOURCE / 'admin_permissions.py').read_text()
    for code in (supervisor, checker):
        ast.parse(code)
    rule = (f'{installation.user} ALL=(root) NOPASSWD: /usr/local/bin/fanvue-admin restart-service, '
            '/usr/local/bin/fanvue-admin reboot, /usr/local/bin/fanvue-admin update\n')
    return {WRAPPER: (wrapper, 0o755), SUPERVISOR: (supervisor, 0o755), CHECKER: (checker, 0o755), SUDOERS: (rule, 0o440)}


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.mp-creatorstudio-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(content)
            os.fchmod(file.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install(installation):
    files = rendered_files(installation)
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='fix-update-', dir=BACKUPS))
    previous = {}
    for index, target in enumerate(files):
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f'{target}: Unerwartete Verknüpfung oder Dateiart. Es wurde nichts geändert.')
        saved = backup / str(index)
        if target.exists():
            shutil.copy2(target, saved)
            previous[target] = (saved, target.stat().st_uid, target.stat().st_gid)
        else:
            previous[target] = None
    (backup / 'manifest.json').write_text(json.dumps({str(p): str(v[0].name) if v else None for p, v in previous.items()}, indent=2))
    staged_rule = backup / 'sudoers-check'
    staged_rule.write_text(files[SUDOERS][0])
    staged_rule.chmod(0o440)
    staged_wrapper = backup / 'wrapper-check'
    staged_wrapper.write_text(files[WRAPPER][0])
    run(['visudo', '-c', '-q', '-f', staged_rule])
    run(['bash', '-n', staged_wrapper])
    changed = []
    try:
        for target, (content, mode) in files.items():
            atomic_write(target, content, mode)
            changed.append(target)
        run(['visudo', '-c', '-q'])
        run(['runuser', '-u', installation.user, '--', '/usr/bin/python3', CHECKER])
    except Exception:
        for target in reversed(changed):
            old = previous[target]
            if old:
                saved, uid, gid = old
                atomic_write(target, saved.read_text(), saved.stat().st_mode & 0o777)
                os.chown(target, uid, gid)
            else:
                target.unlink(missing_ok=True)
        raise RuntimeError(f'Einrichtung fehlgeschlagen. Vorherige Helfer und sudo-Regel wurden wiederhergestellt. Sicherung: {backup}')
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', help='Nur bei mehreren Installationen: systemd-Dienstname')
    parser.add_argument('--check', action='store_true', help='Nur Installation und Voraussetzungen prüfen')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Bitte mit sudo ausführen.')
    installation = discover(args.service)
    print(f'Erkannt: {installation.service}\nBenutzer: {installation.user}\nOrdner: {installation.repo}\nPort: {installation.port}', flush=True)
    validate_runtime(installation)
    if args.check:
        print('Voraussetzungen geprüft. Keine Installation und kein Neustart ausgeführt.')
        return
    with open('/run/lock/mp-creatorstudio-update.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Ein Update oder eine Reparatur läuft bereits. Bitte nach deren Abschluss erneut versuchen.') from exc
        backup = install(installation)
    print(f'Updatefunktion repariert. Alle drei Aktionen sind ohne Passwort freigegeben.\nSicherung: {backup}\nDer laufende Bot wurde nicht neu gestartet. Jetzt im Browser das Update starten.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Fehler: {exc}', file=sys.stderr)
        sys.exit(1)
