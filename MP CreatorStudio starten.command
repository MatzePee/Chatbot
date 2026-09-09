#!/usr/bin/env bash
cd "$(dirname "$0")"
./run.sh --browser --preview
result=$?
if [ "$result" -ne 0 ]; then
  echo "Start fehlgeschlagen. Die Fehlermeldung steht oben."
  read -r -p "Mit Enter schließen … "
fi
exit "$result"
