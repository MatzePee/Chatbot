"""Check passwordless permissions without executing a system action."""
import os
import subprocess

ACTIONS = ('restart-service', 'reboot', 'update')


def check():
    missing = []
    for action in ACTIONS:
        # A successful `sudo -l command` alone also permits password-required
        # commands. Inspect the matched rule's authentication option instead.
        result = subprocess.run(
            ['sudo', '-n', '-ll', '/usr/local/bin/fanvue-admin', action],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, 'LC_ALL': 'C'},
        )
        options = {option.strip() for line in result.stdout.splitlines()
                   if line.strip().startswith('Options:')
                   for option in line.split(':', 1)[1].split(',')}
        if result.returncode or '!authenticate' not in options:
            missing.append(action)
    return missing


if __name__ == '__main__':
    missing = check()
    if missing:
        raise SystemExit('Passwortlose Freigabe fehlt: ' + ', '.join(missing))
    print('Passwortlose Freigaben für Update und Neustarts geprüft; keine Aktion ausgeführt.')
