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
import pwd
import stat
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
    """Matched stopped-installation backup, including the selected generation.

    Restoring this is a separate operator action: automatic code rollback NEVER
    restores data, since active generations may already contain newer writes.
    """
    from uuid import UUID
    repo = Path(repo).absolute()
    roots = ['data/bot.db', 'data/autoposter', '.env', 'data/autoposter.env', 'exports',
             'data/character-active.json', 'data/character-upgrade.json']
    pointer = repo / 'data/character-active.json'
    if pointer.exists():
        value = json.loads(pointer.read_text())
        identity = str(UUID(value['id']))
        if identity != value['id'] or value.get('version') != 1:
            raise RuntimeError('Unknown active generation receipt')
        generation = 'data/character-releases/' + identity
        for relative in ('active', 'configuration', 'exports'):
            path = repo / generation / relative
            if relative == 'active' and not path.is_dir():
                raise RuntimeError('Active generation missing; refusing update')
            roots.append(generation + '/' + relative)
    files = {}
    for relative in roots:
        source = repo / relative
        if source.is_symlink() or source.resolve() != source:
            raise RuntimeError('Unsafe backup source path; refusing update')
        if not source.exists():
            continue
        for path in ([source] if source.is_file() else source.rglob('*')):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise RuntimeError('Unsafe backup file; refusing update')
            if path.is_file():
                files[str(path.relative_to(repo))] = path
    destination.mkdir(parents=True, mode=0o700)
    (destination / '.incomplete').touch(mode=0o600)
    for relative, source in sorted(files.items()):
        # SQLite backup consumes WAL consistently; sidecars are not replayed on
        # that independently verified backup. Non-DB media/config copy bytewise.
        if source.name.endswith(('.db-wal', '.db-shm', '.db-journal')):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source.suffix == '.db':
            with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, \
                 contextlib.closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Datensicherung konnte nicht geprüft werden.')
        else:
            shutil.copy2(source, target)
        target.chmod(0o600)
    (destination / '.incomplete').unlink()


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


HELPER_VERSION = 4
NAMESPACE_PROPERTIES = ('BindPaths', 'BindReadOnlyPaths', 'TemporaryFileSystem', 'InaccessiblePaths',
    'ReadOnlyPaths', 'ReadWritePaths', 'ProtectHome', 'ProtectSystem', 'PrivateTmp', 'PrivateUsers',
    'PrivateMounts', 'NetworkNamespacePath', 'MountImages', 'ExtensionImages',
    'LoadCredential', 'LoadCredentialEncrypted', 'SetCredential', 'SetCredentialEncrypted', 'ImportCredential')
SERVICE_PROPERTIES = ('User', 'Group', 'WorkingDirectory', 'ExecStart', 'ActiveState', 'SubState',
                      'MainPID', 'ControlPID', 'Environment', 'EnvironmentFiles', 'PassEnvironment',
                      'UnsetEnvironment', 'PAMName', 'RootDirectory', 'RootImage', 'ExecSearchPath',
                      'DynamicUser', 'SupplementaryGroups', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload') + NAMESPACE_PROPERTIES


def _resolve_opaque_credentials(props, service):
    """Prove opaque structured arrays empty through typed D-Bus, or refuse."""
    import shlex
    signatures = {'LoadCredential': 'a(ss)', 'LoadCredentialEncrypted': 'a(ss)',
                  'SetCredential': 'a(say)', 'SetCredentialEncrypted': 'a(say)'}
    opaque = [key for key in signatures if props.get(key) == '[unprintable]']
    if not opaque:
        return
    unit = service if service.endswith('.service') else service + '.service'
    reply = shlex.split(command(['busctl', 'call', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager', 'GetUnit', 's', unit]))
    if (len(reply) != 2 or reply[0] != 'o' or not re.fullmatch(
            r'/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+', reply[1])):
        raise RuntimeError('Service credential inspection unsupported; activation refused')
    for key in opaque:
        value = command(['busctl', 'get-property', 'org.freedesktop.systemd1', reply[1],
                         'org.freedesktop.systemd1.Service', key])
        if shlex.split(value) != [signatures[key], '0']:
            raise RuntimeError('Service credentials configured or inspection incomplete; activation refused')
        props[key] = ''


def service_contract(repo, user, service):
    """Conservative activation contract. No environment-file parsing or path guessing."""
    import grp
    import shlex
    repo = Path(repo).absolute()
    account = pwd.getpwnam(user)
    output = command(['systemctl', 'show', service, '--property=' + ','.join(SERVICE_PROPERTIES)])
    props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if set(SERVICE_PROPERTIES) - props.keys():
        raise RuntimeError('Service environment inspection incomplete; activation refused')
    if (account.pw_uid == 0 or props['User'] not in {user, str(account.pw_uid)}
            or props['WorkingDirectory'] != str(repo) or props['DynamicUser'] != 'no'):
        raise RuntimeError('Service identity/path unsupported; activation refused')
    if props['Group'] not in {'', str(account.pw_gid), grp.getgrgid(account.pw_gid).gr_name}:
        raise RuntimeError('Service Group override unsupported; activation refused')
    # systemd, NOT a shell/python dotenv parser, resolved Environment and the
    # standard mandatory .env file into the live process. Accept only this one
    # file spelling; custom file lists/specifiers/optional files need review.
    if props['EnvironmentFiles'] not in {'', str(repo / '.env') + ' (ignore_errors=no)'}:
        raise RuntimeError('Service EnvironmentFiles unsupported; activation refused before stop')
    for key in ('PassEnvironment', 'UnsetEnvironment', 'PAMName', 'RootDirectory',
                'RootImage', 'ExecSearchPath', 'SupplementaryGroups'):
        if props[key]:
            raise RuntimeError('Service environment/namespace overrides unsupported; activation refused before stop')
    _resolve_opaque_credentials(props, service)
    if any(props[key] not in {'', 'no'} for key in NAMESPACE_PROPERTIES):
        raise RuntimeError('Service namespace/credentials unsupported; activation refused before stop')
    if props['DropInPaths'] or props['NeedDaemonReload'] not in {'', 'no'}:
        raise RuntimeError('Service unit changes/drop-ins require coordinated review')
    # Manager defaults can be changed live independently of the unit/files;
    # no trustworthy unchanged-since-exec evidence exists for that case.
    props['_ManagerEnvironment'] = command(['systemctl', 'show', '--property=DefaultEnvironment', '--value'])
    if props['_ManagerEnvironment']:
        raise RuntimeError('Service manager environment unsupported; activation refused')
    match = re.search(r'argv\[\]=(.*?)\s*;\s*(?:ignore_errors|start_time)=', props['ExecStart'])
    args = shlex.split(match[1]) if match else []
    expected = str(repo / '.venv/bin/python')
    if args[:4] != [expected, '-m', 'uvicorn', 'app.main:app'] or any(
            a == '--env-file' or a.startswith('--env-file=') or a in {'--app-dir', '--factory'}
            or a.startswith('--app-dir=') for a in args[4:]):
        raise RuntimeError('Service ExecStart unsupported; activation refused')
    # ExecStart also contains systemd's changing pid/start/stop/exit metadata.
    # Preserve the validated static argv separately for before/after checks.
    props['_ExecStartArguments'] = json.dumps(args)
    # Retain path, ignore_errors and any unknown/static fields. Only these
    # explicitly named runtime fields may vary during the stop operation.
    props['_ExecStartStatic'] = re.sub(
        r';\s*(?:start_time|stop_time|pid|code|status)=[^;{}]*', '', props['ExecStart'])
    return props


def configuration_files(repo, *, started_ns=None, fragment=None):
    """Pin opaque bytes; NEVER interpret systemd EnvironmentFile syntax.

    Refuse files modified since exec: /proc environ is the original exec env,
    whereas a restart would reread files. A conservative tick-boundary refusal
    is preferable to accepting unprovable old/new configuration equivalence.
    """
    import hashlib
    result = {}
    paths = {relative: repo / relative for relative in ('.env', 'data/autoposter.env')}
    if fragment:
        if not Path(fragment).is_absolute():
            raise RuntimeError('Service configuration fragment path unsupported')
        paths['unit'] = Path(fragment)
    for relative, path in paths.items():
        if path.resolve() != path:
            raise RuntimeError('Service configuration path unsafe')
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            result[relative] = None
            continue
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1048576:
                raise RuntimeError('Service configuration type/size unsupported')
            if started_ns is not None and max(before.st_mtime_ns, before.st_ctime_ns) >= started_ns:
                raise RuntimeError('Service configuration changed since start; restart normally before staging')
            value = os.read(fd, 1048577)
            after = os.fstat(fd)
            signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_uid, st.st_gid, st.st_mode)
            if signature(before) != signature(after) or len(value) != before.st_size:
                raise RuntimeError('Service configuration changed during inspection')
            result[relative] = (signature(after), hashlib.sha256(value).hexdigest())
        finally:
            os.close(fd)
    return result


class CapturedEnvironment(dict):
    """Private in-memory attestation. Never serialize/log environment values."""
    def __init__(self, env, *, groups, properties):
        super().__init__(env)
        self.groups = tuple(sorted(set(groups)))
        self.properties = dict(properties)


def running_environment(props, user):
    """Capture systemd's resolved exec environment, not dotenv/shell syntax."""
    import shlex
    account = pwd.getpwnam(user)
    pid = props['MainPID']
    if (not pid.isdigit() or int(pid) <= 0 or props['ActiveState'] != 'active'
            or props['SubState'] != 'running' or props['ControlPID'] != '0'):
        raise RuntimeError('Service must be running for environment parity inspection')
    process = Path('/proc') / pid
    repo = Path(props['WorkingDirectory'])
    try:
        before = (process / 'stat').read_bytes().rsplit(b')', 1)[1].split()[19]
        status = dict(line.split(':', 1) for line in (process / 'status').read_text().splitlines() if ':' in line)
        if (process.stat().st_uid != account.pw_uid
                or set(map(int, status['Uid'].split())) != {account.pw_uid}
                or set(map(int, status['Gid'].split())) != {account.pw_gid}):
            raise RuntimeError('Service process identity mismatch')
        groups = tuple(sorted(set(map(int, status['Groups'].split()))))
        if set(groups) != set(os.getgrouplist(user, account.pw_gid)):
            raise RuntimeError('Service process groups mismatch')
        match = re.search(r'argv\[\]=(.*?)\s*;\s*(?:ignore_errors|start_time)=', props['ExecStart'])
        expected = shlex.split(match[1]) if match else []
        actual = os.fsdecode((process / 'cmdline').read_bytes()).rstrip('\0').split('\0')
        if (actual != expected or (process / 'exe').resolve(strict=True) != (repo / '.venv/bin/python').resolve(strict=True)
                or (process / 'cwd').resolve(strict=True) != repo):
            raise RuntimeError('Service launched program/path mismatch')
        entries = (process / 'environ').read_bytes().split(b'\0')
        env = dict(entry.decode().split('=', 1) for entry in entries if entry)
        if len(env) != sum(bool(entry) for entry in entries) or any(not k or '=' in k for k in env):
            raise RuntimeError('Service environment malformed')
        after = (process / 'stat').read_bytes().rsplit(b')', 1)[1].split()[19]
        if before != after:
            raise RuntimeError('Service process changed during environment inspection')
        captured = CapturedEnvironment(env, groups=groups, properties=props)
        captured.start = before
        started_ns = time.time_ns() - int((time.clock_gettime(time.CLOCK_BOOTTIME)
                     - int(before) / os.sysconf('SC_CLK_TCK')) * 1_000_000_000)
        if not props['FragmentPath']:
            raise RuntimeError('Service configuration fragment unavailable')
        captured.files = configuration_files(repo, started_ns=started_ns, fragment=props['FragmentPath'])
        if captured.files.get('unit') is None:
            raise RuntimeError('Service configuration fragment unavailable')
        if props['EnvironmentFiles'] and captured.files['.env'] is None:
            raise RuntimeError('Service EnvironmentFile missing')
        return captured
    except (OSError, ValueError, UnicodeError, IndexError, KeyError) as exc:
        raise RuntimeError('Service environment unavailable; activation refused') from exc


def configuration_probe(repo, user, environment):
    """Mutable app code runs ONLY after dropping UID/GID/groups, never as root."""
    account = pwd.getpwnam(user)
    result = subprocess.run([str(repo / '.venv/bin/python'), '-m', 'app.character_upgrade', '--inspect', str(repo)],
        cwd=repo, env=dict(environment), user=account.pw_uid, group=account.pw_gid,
        extra_groups=environment.groups, capture_output=True, timeout=30)
    digest = result.stdout.strip()
    if result.returncode or not re.fullmatch(rb'[0-9a-f]{64}', digest):
        raise RuntimeError('Service application configuration/path unsupported; refused before stop')
    return digest.decode('ascii')


def validate_capture(repo, user, service, environment, *, stopped=False):
    current = service_contract(repo, user, service)
    ignored = {'ExecStart'}
    if stopped:
        ignored.update({'ActiveState', 'SubState', 'MainPID', 'ControlPID'})
    if ({k:v for k,v in current.items() if k not in ignored}
            != {k:v for k,v in environment.properties.items() if k not in ignored}
            or configuration_files(repo, fragment=current['FragmentPath']) != environment.files):
        raise RuntimeError('Service environment/configuration changed; activation refused')
    if not stopped:
        checked = running_environment(current, user)
        if (checked.start != environment.start or dict(checked) != dict(environment)
                or checked.groups != environment.groups):
            raise RuntimeError('Service process changed before stop')
    return current


def stopped_migrate(repo, user, service, *, environment=None):
    """Fixed pre-import operation, with a root-only stopped-source capability.

    Never load repository Python as root. A global privileged /proc scan precedes
    the direct service-UID child; that child still rejects every visible writer.
    The capability exists only as an inherited, unlinked read-only descriptor.
    """
    if os.geteuid() != 0:
        raise RuntimeError('Stopped migration requires the root service supervisor')
    if environment is None:
        raise RuntimeError('Captured service environment required; never inherit root environment')
    account = pwd.getpwnam(user)
    repo = Path(repo).absolute()
    source = repo / 'data'
    if account.pw_uid == 0 or repo.resolve() != repo or source.resolve() != source:
        raise RuntimeError('Fixed non-root installation without symlinks required')
    if repo.stat().st_uid != account.pw_uid or source.stat().st_uid != account.pw_uid:
        raise RuntimeError('Configured source owner does not match the service user')
    properties = (validate_capture(repo, user, service, environment, stopped=True)
                  if isinstance(environment, CapturedEnvironment) else service_contract(repo, user, service))
    if properties['EnvironmentFiles'] and not isinstance(environment, CapturedEnvironment):
        raise RuntimeError('Attested service EnvironmentFile required')
    if properties.get('User') not in {user, str(account.pw_uid)} or properties.get('WorkingDirectory') != str(repo):
        raise RuntimeError('Service identity/path changed; migration refused')
    if (properties.get('ActiveState') != 'inactive' or properties.get('SubState') != 'dead'
            or properties.get('MainPID') != '0' or properties.get('ControlPID') != '0'):
        raise RuntimeError('Service is not verified stopped; migration refused')
    identities = set()
    for path in source.rglob('*'):
        # Prepared virtualenvs legitimately contain interpreter symlinks. They
        # are not runtime migration input; the child rejects DB/media there.
        if path.relative_to(source).parts[0] in {'releases', 'backups'}:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeError('Unsafe stopped source path')
        if stat.S_ISREG(info.st_mode):
            identities.add((info.st_dev, info.st_ino))
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
            for descriptor in descriptors:
                try:
                    info = descriptor.stat()
                except FileNotFoundError:
                    continue
                if (info.st_dev, info.st_ino) in identities:
                    raise RuntimeError('Source is open by a process; migration refused')
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise RuntimeError('Privileged source verification unavailable; migration refused') from exc
    with tempfile.TemporaryFile() as proof:
        proof.write(json.dumps({'parent': os.getpid(), 'uid': account.pw_uid,
                    'source': str(source), 'created': time.monotonic()}).encode())
        proof.flush()
        os.fchmod(proof.fileno(), 0o400)
        readonly = os.open('/proc/self/fd/' + str(proof.fileno()), os.O_RDONLY)
        try:
            env = {**environment, 'MP_STOPPED_SOURCE_FD': str(readonly)}
            if isinstance(environment, CapturedEnvironment):
                if not getattr(environment, 'configuration', None):
                    raise RuntimeError('Service configuration attestation missing')
                env['MP_EXPECTED_CONFIGURATION_SHA256'] = environment.configuration
            result = subprocess.run([str(repo / '.venv/bin/python'), '-m', 'app.character_upgrade', str(repo)],
                        cwd=repo, env=env, user=account.pw_uid, group=account.pw_gid,
                        extra_groups=(environment.groups if isinstance(environment, CapturedEnvironment)
                                      else os.getgrouplist(user, account.pw_gid)),
                        pass_fds=(readonly,), capture_output=True, timeout=1200)
            if result.returncode:
                raise RuntimeError('Stopped migration refused; originals retained; inspect offline paths/schema')
        finally:
            os.close(readonly)


def migration_request(repo, user):
    """Read bounded unprivileged intent through pinned no-follow descriptors."""
    account = pwd.getpwnam(user)
    try:
        with contextlib.ExitStack() as stack:
            parent = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, parent)
            data = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, data)
            try:
                fd = os.open('character-upgrade.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=data)
            except FileNotFoundError:
                return {}
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid
                    or info.st_mode & 0o022 or info.st_size > 8192):
                raise ValueError('Invalid request file')
            raw = os.read(fd, 8193)
            if len(raw) > 8192:
                raise ValueError('Oversize request')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid request payload')
            return value
    except (OSError, ValueError) as exc:
        raise RuntimeError('Unsafe migration request; no service action authorized') from exc


def restart(repo, user, service, *, environment=None):
    """The existing fixed restart action owns the stop -> pre-import -> start seam."""
    if os.geteuid() != 0 or pwd.getpwnam(user).pw_uid == 0:
        raise RuntimeError('Root supervisor and fixed non-root service user required')
    repo = Path(repo).absolute()
    if repo.resolve() != repo or (repo / 'data').resolve() != repo / 'data':
        raise RuntimeError('Unsafe installation path')
    migrating = (not (repo / 'data/character-active.json').exists()
                 and migration_request(repo, user).get('phase') == 'pending_restart')
    if migrating:
        props = service_contract(repo, user, service)
        if environment is None:
            environment = running_environment(props, user)
            validate_capture(repo, user, service, environment)
            environment.configuration = configuration_probe(repo, user, environment)
            validate_capture(repo, user, service, environment)
    def report(phase):
        command(['runuser', '-u', user, '--', sys.executable, Path(__file__).resolve(),
                 'restart-status', repo, phase])
    report('checking')
    command(['systemctl', 'stop', service], timeout=120)
    try:
        if (not (repo / 'data/character-active.json').exists()
                and migration_request(repo, user).get('phase') == 'pending_restart'):
            stopped_migrate(repo, user, service, environment=environment)
        command(['systemctl', 'start', service], timeout=120)
        expected = command(['runuser', '-u', user, '--', 'git', '-C', repo, 'rev-parse', 'HEAD'])
        if not healthy(expected, False):
            raise RuntimeError('Restart health failed; active data retained; no reverse migration')
        report('complete')
    except (Exception, KeyboardInterrupt):
        # A pre-pointer failure can safely return to the original legacy app.
        # A published generation may have new writes: NEVER remove its pointer.
        report('failed')
        if not (repo / 'data/character-active.json').exists():
            command(['systemctl', 'start', service], timeout=120)
        raise


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
    if action == 'readiness':
        try:
            props = service_contract(repo, *extra)
            running_environment(props, extra[0])
            result = {'ready': True}
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
            result = {'ready': False, 'reason': 'service_contract_refused'}
        print(json.dumps(result))
    elif action in {'run', 'restart'}:
        with open('/run/lock/mp-creatorstudio-update.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (supervise if action == 'run' else restart)(repo, *extra)
    elif action == 'restart-status':
        phase, = extra
        save(repo / 'data/restart-status.json', {'phase': phase, 'updated_at': time.time(),
             'message': ('Restart failed; original/active data retained. No automatic reverse migration.'
                         if phase == 'failed' else 'Restart ' + phase)})
        request = repo / 'data/character-upgrade.json'
        if phase == 'failed' and not (repo / 'data/character-active.json').exists() and request.exists():
            value = json.loads(request.read_text())
            if value.get('phase') == 'pending_restart':
                save(request, {**value, 'phase': 'failed', 'message': 'Stopped migration refused; originals retained.'})
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
