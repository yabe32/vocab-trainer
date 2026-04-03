#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 - <<'PY'
from pathlib import Path

for p in Path(".").rglob("*.py"):
    data = p.read_bytes()
    cleaned = data.replace(b"\xef\xbb\xbf", b"")
    if cleaned != data:
        p.write_bytes(cleaned)
        print(f"BOM entfernt: {p}")
PY

