"""Read-only installed privileged-helper readiness; never import app-owned root code."""
import ast
import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

WRAPPER = Path('/usr/local/bin/fanvue-admin')
SUPERVISOR = Path('/usr/local/libexec/mp-creatorstudio-update')
MIN_VERSION = 4
ADMIN_MESSAGE = ('Administrator erforderlich: installierten root-eigenen Update-Helfer aus einer '
                 'separat geprüften, root-eigenen Releasekopie mit '
                 'sudo bash deploy/install-update-helper.sh --service DIENSTNAME einrichten. '
                 'Ein GitHub-Codeupdate ersetzt diesen Helfer nicht. Keine Umstellung vorgemerkt.')


@contextlib.contextmanager
def trusted_file(path):
    """Pin every component with nofollow; mutable ancestors invalidate root trust."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Untrusted helper path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, component in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            expected = stat.S_ISREG if index == len(path.parts)-2 else stat.S_ISDIR
            if info.st_uid != 0 or info.st_mode & 0o022 or not expected(info.st_mode):
                raise ValueError('Untrusted helper ownership/type')
        yield fd, os.pread(fd, 131073, 0).decode('utf-8')
    finally:
        os.close(fd)


def readiness(root):
    result = {'ready': False, 'required_version': MIN_VERSION, 'installed_version': None,
              'reason': 'missing_or_incompatible_helper', 'message': ADMIN_MESSAGE}
    try:
        with trusted_file(WRAPPER) as (_, wrapper), trusted_file(SUPERVISOR) as (fd, supervisor):
            if max(len(wrapper), len(supervisor)) > 131072:
                return result
            assignments = [n for n in ast.parse(supervisor).body if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == 'HELPER_VERSION' for t in n.targets)]
            if len(assignments) != 1:
                return result
            version = ast.literal_eval(assignments[0].value)
            if type(version) is not int:
                return result
            result['installed_version'] = version
            fixed = {}
            for key in ('REPO', 'SVC_USER', 'SERVICE', 'HELPER_PROTOCOL'):
                values = re.findall('^' + key + '=(.*)$', wrapper, re.M)
                if len(values) != 1 or len(words := shlex.split(values[0])) != 1:
                    return result
                fixed[key] = words[0]
            if (version != MIN_VERSION or fixed['HELPER_PROTOCOL'] != str(MIN_VERSION)
                    or fixed['REPO'] != str(Path(root).absolute())
                    or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', fixed['SVC_USER'])
                    or not re.fullmatch(r'[A-Za-z0-9_.@:-]+', fixed['SERVICE'])):
                return result
            check = subprocess.run(['/usr/bin/python3', '-I', '/proc/self/fd/' + str(fd),
                                    'readiness', fixed['REPO'], fixed['SVC_USER'], fixed['SERVICE']],
                                   pass_fds=(fd,), capture_output=True, text=True, timeout=15,
                                   env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
            if check.returncode == 0 and json.loads(check.stdout).get('ready') is True:
                result.update(ready=True, reason='compatible', message='Installierter Helfer und Dienstkonfiguration geprüft. Umstellung bleibt separat.')
            else:
                result.update(reason='service_contract_refused', message='Administrator erforderlich: Dienstumgebung oder Startpfade werden nicht sicher unterstützt; keine Umstellung vormerken. Siehe docs/helper-acceptance.md.')
    except (OSError, ValueError, SyntaxError, TypeError, subprocess.SubprocessError):
        pass
    return result
