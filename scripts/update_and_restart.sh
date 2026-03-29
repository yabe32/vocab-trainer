#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
git pull

source .venv/bin/activate
pip install -r requirements.txt

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^vokabeltrainer\.service'; then
  sudo systemctl restart vokabeltrainer
  sudo systemctl status vokabeltrainer --no-pager
else
  echo "Kein systemd-Service 'vokabeltrainer' gefunden."
  echo "Starte stattdessen manuell mit: ./scripts/run_gunicorn.sh"
fi
