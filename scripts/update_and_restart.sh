#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
git pull

source .venv/bin/activate
pip install -r requirements.txt

./scripts/restart_app.sh
