#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .autoupdate_loop.pid ]; then
  echo "Keine PID-Datei gefunden. Es laeuft vermutlich kein Auto-Update-Loop."
  exit 0
fi

PID="$(cat .autoupdate_loop.pid || true)"
if [ -z "${PID}" ]; then
  rm -f .autoupdate_loop.pid
  echo "PID-Datei war leer und wurde entfernt."
  exit 0
fi

if ps -p "${PID}" >/dev/null 2>&1; then
  kill "${PID}"
  echo "Auto-Update-Loop mit PID ${PID} gestoppt."
else
  echo "Prozess ${PID} lief nicht mehr."
fi

rm -f .autoupdate_loop.pid
