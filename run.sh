#!/usr/bin/env bash
# Startskript fuer den Fanvue-Chatbot (macOS + Ubuntu).
set -e
cd "$(dirname "$0")"

VENV=".venv"

# Pruefen, ob ein vorhandener venv auf DIESER Plattform funktioniert.
# (Falls z.B. ein venv einer anderen Plattform mitgeliefert wurde, neu bauen.)
venv_ok() {
  [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import ensurepip" >/dev/null 2>&1
}

if [ -d "$VENV" ] && ! venv_ok; then
  echo "Vorhandene virtuelle Umgebung ist nicht nutzbar - baue sie neu..."
  rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
  echo "Erstelle virtuelle Umgebung..."
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

# .env laden, falls vorhanden
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "Starte Fanvue-Chatbot auf http://${HOST}:${PORT}"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
