#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
git pull --rebase --autostash

source .venv/bin/activate
pip install -r requirements.txt
chmod +x ./scripts/sanitize_python_sources.sh
./scripts/sanitize_python_sources.sh
python3 -m py_compile app.py

./scripts/restart_app.sh
