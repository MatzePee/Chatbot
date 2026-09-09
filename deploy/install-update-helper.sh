#!/usr/bin/env bash
# One-time upgrade preparation; does not restart the service or change its checkout.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Bitte mit sudo starten.' >&2; exit 1; }
SOURCE="$(cd "$(dirname "$0")" && pwd)"
REPO="/srv/fanvue/Fanvue_Chatbot"
SVC_USER="matze"
SERVICE="fanvue-chatbot"
[[ -f "$REPO/data/bot.db" && -x "$REPO/.venv/bin/python" ]]
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ erforderlich"'
STAMP="$(date +%Y%m%d-%H%M%S)"
install -d -m 700 /var/backups/mp-creatorstudio
if [[ -f /usr/local/bin/fanvue-admin ]]; then
  cp -p /usr/local/bin/fanvue-admin "/var/backups/mp-creatorstudio/fanvue-admin-$STAMP"
fi
/usr/bin/python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$SOURCE/creatorstudio_update.py"
bash -n "$SOURCE/fanvue-admin"
install -d -m 755 /usr/local/libexec
install -o root -g root -m 755 "$SOURCE/creatorstudio_update.py" /usr/local/libexec/mp-creatorstudio-update
install -o root -g root -m 755 "$SOURCE/fanvue-admin" /usr/local/bin/fanvue-admin
# Keep the existing, narrowly scoped sudo rule.
visudo -c -q
printf '%s\n' 'Update-Vorbereitung installiert. Laufender Dienst und Daten unverändert.'
systemctl is-active "$SERVICE"
