#!/usr/bin/env bash
set -euo pipefail

if [ ! -f ".env" ]; then
  echo "FLASK_SECRET_KEY=bitte-hier-einen-langen-zufallswert-setzen" > .env
  echo "PORT=8090" >> .env
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup abgeschlossen."
echo "Starte mit: scripts/run_gunicorn.sh"
