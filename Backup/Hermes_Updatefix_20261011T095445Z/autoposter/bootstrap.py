"""Create private AutoPost keys only for a genuinely new installation."""
import base64
import os
from pathlib import Path
import secrets


def ensure_config(root: Path) -> None:
    path = root / 'data/autoposter.env'
    if path.exists():
        return  # Existing keys may encrypt credentials; never replace them.
    path.parent.mkdir(parents=True, exist_ok=True)
    content = ('# Private AutoPost configuration; excluded from Git.\n'
               f'MP_AUTOPOSTER_SECRET_KEY={secrets.token_hex(32)}\n'
               f'MP_AUTOPOSTER_ENCRYPTION_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}\n'
               'MP_AUTOPOSTER_DRY_RUN=true\nMP_AUTOPOSTER_GLOBAL_PAUSE=true\n')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)

