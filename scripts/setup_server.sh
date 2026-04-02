#!/usr/bin/env bash
set -euo pipefail

if [ ! -f ".env" ]; then
  SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  ADMIN_CODE="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(3))
PY
)"
  LEARNER_CODE="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(3))
PY
)"
  echo "FLASK_SECRET_KEY=${SECRET}" > .env
  echo "ADMIN_ACCESS_CODE=${ADMIN_CODE}" >> .env
  echo "LEARNER_ACCESS_CODE=${LEARNER_CODE}" >> .env
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
echo "ADMIN_ACCESS_CODE und LEARNER_ACCESS_CODE wurden in .env gesetzt."
echo "Starte mit: scripts/run_gunicorn.sh"
