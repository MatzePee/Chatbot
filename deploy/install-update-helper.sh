#!/usr/bin/env bash
# Existing-installation repair. Settings are discovered from systemd.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")" && pwd)"
exec /usr/bin/python3 "$SOURCE/repair_update.py" "$@"
