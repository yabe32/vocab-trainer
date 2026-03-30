#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .autoupdate_loop.pid ]; then
  OLD_PID="$(cat .autoupdate_loop.pid || true)"
  if [ -n "${OLD_PID}" ] && ps -p "${OLD_PID}" >/dev/null 2>&1; then
    echo "Auto-Update-Loop laeuft bereits mit PID ${OLD_PID}."
    exit 0
  fi
fi

nohup bash -c 'while true; do ./scripts/auto_update.sh; sleep 60; done' \
  > .autoupdate_loop.log 2>&1 &

echo $! > .autoupdate_loop.pid
echo "Auto-Update-Loop gestartet. PID: $(cat .autoupdate_loop.pid)"
echo "Log: $(pwd)/.autoupdate_loop.log"
