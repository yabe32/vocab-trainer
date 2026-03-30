#!/usr/bin/env bash
set -euo pipefail

if [ ! -f ".env" ]; then
  echo "FLASK_SECRET_KEY=bitte-hier-einen-langen-zufallswert-setzen" > .env
  echo "PORT=8090" >> .env
  echo "VOKABEL_DATEI=data/vokabeln.csv" >> .env
fi

mkdir -p data
if [ ! -f "data/vokabeln.csv" ]; then
  if [ -f "data/vokabeln.seed.csv" ]; then
    cp data/vokabeln.seed.csv data/vokabeln.csv
  elif [ -f "vokabeln.csv" ]; then
    cp vokabeln.csv data/vokabeln.csv
  fi
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup abgeschlossen."
echo "Starte mit: scripts/run_gunicorn.sh"
