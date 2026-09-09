"""Check passwordless permissions without executing a system action."""
import fnmatch
import os
import pwd
import re
import subprocess

ACTIONS = ('restart-service', 'reboot', 'update')
HELPER = '/usr/local/bin/fanvue-admin'


def _root_target(value):
    """True/False for resolved targets; None for unsupported aliases/groups."""
    matches = False
    for item in value.split(','):
        item = item.strip()
        denied = item.startswith('!')
        name = item.lstrip('!')
        if name in ('ALL', 'root', '#0'):
            matches = not denied
        elif name.startswith('#') and name[1:].isdigit():
            continue
        else:
            try:
                if pwd.getpwnam(name).pw_uid == 0:
                    matches = not denied
            except KeyError:
                matches = None
    return matches


def _command_match(line, action):
    """Return (possibly matches, explicit grant). Unclear patterns cannot grant access."""
    denied = line.startswith('!')
    command = line.lstrip('!').strip()
    if command == 'ALL':
        return True, not denied
    parts = command.split(None, 1)
    if not parts:
        return False, False
    path = parts[0]
    # Long listings normally expand aliases. Fail closed for other syntax.
    if not path.startswith('/') or '\\' in path:
        return True, False
    if path.endswith('/') and HELPER.startswith(path):
        return True, False
    if not fnmatch.fnmatchcase(HELPER, path):
        return False, False
    if len(parts) == 1:
        return True, not denied and path == HELPER
    arguments = parts[1]
    if arguments.startswith('^') or '\\' in arguments:
        return True, False
    matches = fnmatch.fnmatchcase(action, arguments)
    return matches, matches and not denied and path == HELPER and arguments == action


def passwordless_listing(output, action):
    # With a command argument sudo-rs prints only the command, even with -ll.
    # Read the complete verbose listing instead, keeping each rule's options
    # attached to its commands. A later PASSWD/deny rule must win.
    allowed = False
    for block in re.split(r'(?m)^\s*Sudoers entry:[^\n]*\n', output)[1:]:
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.strip().partition(':')
            if sep and key in ('RunAsUsers', 'RunAsGroups', 'Options'):
                fields[key] = value.strip()
        target = _root_target(fields.get('RunAsUsers', 'UNKNOWN'))
        if target is False:
            continue
        options = {part.strip() for part in fields.get('Options', '').split(',')}
        # The installed rule uses (root), without a group restriction.
        root_group = fields.get('RunAsGroups', '') in ('', 'root', '#0', 'ALL')
        grant = target is True and root_group and '!authenticate' in options and 'authenticate' not in options
        commands = re.split(r'(?m)^\s*Commands:\s*\n', block, maxsplit=1)
        if len(commands) != 2:
            return False
        for line in commands[1].splitlines():
            if not line.strip():
                continue
            matches, explicit = _command_match(line.strip(), action)
            if matches:
                allowed = grant and explicit
    return allowed


def _list(arguments):
    return subprocess.run(
        ['sudo', '-n', *arguments], capture_output=True, text=True, timeout=10,
        env={**os.environ, 'LC_ALL': 'C'},
    )


def check():
    # Never replace these list queries with an invocation of the actual action.
    listing = _list(['-ll'])
    if listing.returncode:
        return list(ACTIONS)
    missing = []
    for action in ACTIONS:
        command = _list(['-l', '-u', 'root', HELPER, action])
        if command.returncode or not passwordless_listing(listing.stdout, action):
            missing.append(action)
    return missing


if __name__ == '__main__':
    try:
        missing = check()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f'sudo-Berechtigungsprüfung nicht möglich: {exc}')
    if missing:
        raise SystemExit('Passwortlose Freigabe nicht bestätigt: ' + ', '.join(missing)
                         + '. sudo-Regeln und Ausgabe von sudo -n -ll prüfen.')
    print('Passwortlose Freigaben für Update und Neustarts geprüft; keine Aktion ausgeführt.')
