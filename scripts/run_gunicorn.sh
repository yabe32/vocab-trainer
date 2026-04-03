#!/usr/bin/env bash
set -euo pipefail

source .venv/bin/activate

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

chmod +x ./scripts/sanitize_python_sources.sh
./scripts/sanitize_python_sources.sh
python3 -m py_compile app.py

PORT="${PORT:-8090}"

exec gunicorn --workers 2 --bind 0.0.0.0:${PORT} wsgi:app
