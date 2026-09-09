#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo 'MP CreatorStudio · Server-Zugang für den Mitlesemodus'
echo 'Der Schlüssel darf ausschließlich den aktuellen Access-Token lesen.'
echo 'Er erlaubt keine Shell, keinen Refresh-Token-Zugriff und keine Änderungen am Bot.'
echo 'Gib gleich das SSH-Passwort von matze auf 192.168.20.16 ein.'
echo 'Die Passworteingabe bleibt unsichtbar und wird nicht gespeichert.'
if ssh -o StrictHostKeyChecking=yes -o ConnectTimeout=8 matze@192.168.20.16 \
  'umask 077; mkdir -p "$HOME/.ssh"; touch "$HOME/.ssh/authorized_keys"; IFS= read -r entry; if ! grep -qF -- "$entry" "$HOME/.ssh/authorized_keys"; then printf "\n%s\n" "$entry" >> "$HOME/.ssh/authorized_keys"; fi' \
  < data/preview-ssh/authorized_key; then
  echo 'Schlüssel hinterlegt. Prüfe ausschließlich den Lesezugang …'
  if ssh -i data/preview-ssh/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes matze@192.168.20.16 read-token | \
    .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d.get("access_token"); print("Lesezugang funktioniert. Der bestehende Bot läuft unverändert weiter.")'; then
    echo 'Jetzt MP CreatorStudio starten.command doppelklicken.'
  else
    echo 'Leseprüfung fehlgeschlagen. Bitte die Fehlermeldung oben prüfen.'
  fi
else
  echo 'Anmeldung fehlgeschlagen. Es wurde kein funktionierender Zugang eingerichtet.'
fi
read -r -p 'Mit Enter schließen … '
