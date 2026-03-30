#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^vokabeltrainer\.service'; then
  sudo systemctl restart vokabeltrainer
  sudo systemctl status vokabeltrainer --no-pager
  exit 0
fi

if pgrep -f "gunicorn.*wsgi:app" >/dev/null 2>&1; then
  pkill -f "gunicorn.*wsgi:app" || true
  sleep 1
fi

nohup ./scripts/run_gunicorn.sh > .gunicorn.log 2>&1 &
echo "Gunicorn neu gestartet (ohne systemd). Logs: .gunicorn.log"
