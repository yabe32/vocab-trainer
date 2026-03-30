#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .autoupdate_loop.pid ]; then
  echo "Auto-Update-Loop: nicht gestartet (keine PID-Datei)."
  exit 0
fi

PID="$(cat .autoupdate_loop.pid || true)"
if [ -n "${PID}" ] && ps -p "${PID}" >/dev/null 2>&1; then
  echo "Auto-Update-Loop laeuft. PID: ${PID}"
  echo "Log: $(pwd)/.autoupdate_loop.log"
else
  echo "Auto-Update-Loop: nicht aktiv (stale PID-Datei)."
fi
