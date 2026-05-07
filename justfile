# instagram-logger — developer recipes for the Phase 3 vertical slice.
# Run `just` to list. Assumes Python 3.11+ and Node 20+ on PATH.

set shell := ["bash", "-cu"]

# Install backend (.venv) and frontend (node_modules) deps.
# Picks the newest Python >= 3.11 available; rejects 3.10 and below.
install:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d .venv ]; then
      PY=""
      for v in python3.14 python3.13 python3.12 python3.11; do
        if command -v "$v" >/dev/null 2>&1; then PY="$v"; break; fi
      done
      if [ -z "$PY" ]; then
        echo "ERROR: need Python >= 3.11 (try: brew install python@3.13)"; exit 1
      fi
      echo "Creating .venv with $PY"
      "$PY" -m venv .venv
    fi
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e '.[dev]'
    cd frontend && npm install

# Run the FastAPI dev server on 127.0.0.1:8000 (migrations apply at startup).
dev:
    .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Interactive Instagram login. Writes data/instagrapi_settings.json.
login:
    .venv/bin/python -m backend.ig_client.login

# Bypass password login by importing a sessionid cookie from a working
# browser tab. Use this when `just login` is rejected (BadPassword /
# device-flagged) but the IG web app loads fine in your browser.
import-session:
    .venv/bin/python -m backend.ig_client.import_session

# One-shot ingest of the first page of "All Posts".
sync:
    .venv/bin/python -m backend.ingest --first-page-only

# Full B1 sync against the offline fake IG fixture (no auth needed).
sync-fake:
    IG_CLIENT=fake .venv/bin/python -m backend.ingest

# Hot-backup the SQLite DB (WAL-safe).
backup:
    mkdir -p data/backups
    sqlite3 data/app.db ".backup data/backups/app-$(date +%Y%m%d-%H%M%S).db"
