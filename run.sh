#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
  PYTHON=""
  for candidate in python3.12 python3.13 python3.11 python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
    if "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
      PYTHON="$candidate"; break
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo 'Bitte Python 3.11 oder neuer installieren und erneut starten.'; exit 1
  fi
  if [ -d .venv ]; then mv .venv ".venv-old-$(date +%Y%m%d%H%M%S)"; fi
  "$PYTHON" -m venv .venv
fi
CURRENT="$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
INSTALLED="$(cat .venv/requirements.sha256 2>/dev/null || true)"
if [ "$CURRENT" != "$INSTALLED" ]; then
  .venv/bin/python -m pip install -r requirements.txt
  echo "$CURRENT" > .venv/requirements.sha256
fi
exec .venv/bin/python start.py "$@"
