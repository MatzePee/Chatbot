#!/usr/bin/env bash
# One-time repair for an existing installation; does not start an update.
# curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/v2.0.2/deploy/fix-update.sh | sudo bash
# Keep all work inside main so a truncated download cannot run half the script.
set -euo pipefail
main() {
  [[ "$(id -u)" == 0 ]] || { echo 'Bitte mit sudo ausführen.' >&2; return 1; }
  command -v curl >/dev/null || { echo 'curl fehlt auf diesem Server.' >&2; return 1; }
  [[ -x /usr/bin/python3 ]] || { echo '/usr/bin/python3 fehlt.' >&2; return 1; }
  local source_base="https://raw.githubusercontent.com/MatzePee/Chatbot/v2.0.2/deploy"
  CREATOR_FIX_TMP="$(mktemp -d /tmp/mp-creatorstudio-fix-XXXXXX)"
  trap 'rm -rf -- "$CREATOR_FIX_TMP"' EXIT
  # Contains public program files only; the service user must read the checker.
  chmod 755 "$CREATOR_FIX_TMP"
  echo 'MP CreatorStudio – Updatefunktion einmalig reparieren'
  echo 'Einrichtungsdateien für v2.0.2 werden geladen …'
  local file
  for file in install-update-helper.sh repair_update.py admin_permissions.py fanvue-admin creatorstudio_update.py; do
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      --connect-timeout 15 --max-time 120 --retry 2 \
      "$source_base/$file" -o "$CREATOR_FIX_TMP/$file"
    [[ -s "$CREATOR_FIX_TMP/$file" ]] || { echo "Datei fehlt: $file" >&2; return 1; }
    chmod 644 "$CREATOR_FIX_TMP/$file"
  done
  # No reads from stdin: it may still be the curl pipeline.
  bash "$CREATOR_FIX_TMP/install-update-helper.sh" "$@" </dev/null
}
main "$@"
