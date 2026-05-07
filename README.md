# instagram-logger — Phase 3 vertical slice

Self-hosted archiver for a single Instagram account's saved posts. This commit
ships the smallest end-to-end demo described in `.omc/plans/plan.md` §9:
a CLI login, a one-shot first-page ingest, a localhost-only API with full
`Range` / `If-None-Match` support on media, and a 3-column React grid.

## Quick start

```bash
# 1. Install backend + frontend deps (uses a local .venv and npm).
just install

# 2. Log in to Instagram once. Prompts for username/password and 2FA if challenged.
#    Writes data/instagrapi_settings.json.
just login

# 3. Pull the first page of "All Posts" (does HTTP + media downloads).
just sync

# 4. Run the API on http://127.0.0.1:8000 (migrations apply at startup).
just dev

# 5. In another terminal, run the frontend dev server.
cd frontend && npm run dev
# Open http://localhost:5173
```

## What the slice covers

- `python -m backend.ig_client.login` — interactive login, settings persist.
- `python -m backend.ingest --first-page-only` — first page of "All Posts",
  per-post `BEGIN IMMEDIATE` transactions, atomic media writes (verified
  Content-Length + fsync + rename), sha256 path layout under `data/media/<aa>/`.
- `uvicorn backend.main:app` — `127.0.0.1` only (refuses non-loopback unless
  `ALLOW_REMOTE=1`). Endpoints: `GET /api/posts`, `GET /api/media/:sha256`
  (full Range + 304), `GET /api/auth/status`.
- React grid at `/`: `grid grid-cols-3 gap-1` of `<img src="/api/media/:sha256">`.

## What it does NOT do yet

Collections enumeration, post detail modal, search/filters, scheduler, 2FA
banner UI, launchd plist — see `.omc/plans/plan.md` §10 batches B1-B6.
