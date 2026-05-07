# Session handoff — instagram-logger

**Date:** 2026-05-06
**Where we are:** Phase 4 (Iterative build), batch B1 complete; ready to start B2.

## Phase status (new-project workflow)

| # | Phase | Status |
|---|---|---|
| P0 | Crystallize → brief | ✅ `.omc/plans/brief.md` |
| P1 | Research | ✅ `.omc/research/summary.md` |
| P2 | Plan + critic v2 | ✅ `.omc/plans/plan.md` (12 sections, all 14 must-fix items applied) |
| P3 | Scaffold + vertical slice | ✅ 38 files, slice runs against fake fixture |
| P4 | Iterative build | 🟡 B1 done; B2 next |
| P5 | Verify & review | ⬜ |
| P6 | Ship | ⬜ |

## What B1 delivered
- Full reconcile algorithm in `backend/ingest/run.py` per plan §4 (per-post `BEGIN IMMEDIATE…COMMIT`, atomic media write via `media/store.py`, `fully_enumerated` gating of unsaved-flagging, dedup via sha256 PK)
- Collections + many-to-many `post_collections` with `last_seen_at` removal policy
- Endpoints: `GET /api/posts` (with `collection_id`/`q`/`since`/`until`/`include_unsaved`/cursor), `GET /api/posts/:id`, `GET /api/collections`, `GET /api/media/:sha256` (Range + 304), `GET /api/auth/status`
- Frontend: `/` (All Posts grid), `/collections/:id` (per-collection grid). Click does not yet open modal — that's B2.
- Fake IG fixture client behind `IG_CLIENT=fake`: 40 posts × 4 collections (All Posts / Inspiration / Recipes / Travel), single-image + carousel + video kinds, dedups to 2 unique media files
- Tests: 8/8 pass via `.venv/bin/pytest -q`

## Live state on disk after the populating sync
- `data/app.db` populated: 40 posts, 60 post_media rows, 65 post_collections rows, 2 media_files (red JPEG + silent MP4), 1 sync_run with `fully_enumerated=1`
- `data/instagrapi_settings.json` does NOT exist (real login still blocked, see below)

## Outstanding live-IG blocker (separate from code)
- Real Instagram login is soft-blocked. `just login` returns `BadPassword` and the browser also rejects the correct password.
- **Recovery path** (must be done by you, not by code):
  1. Log in via the official Instagram **mobile app**
  2. If app rejects too: reset password via email/SMS through Forgot password
  3. After mobile app login succeeds, wait ~1h
  4. Then retry `just login` — expect a `ChallengeRequired`, enter the emailed code
- Until that's done, develop against `IG_CLIENT=fake`.

## Resume commands

```bash
cd /Users/genehan/projects/claudehome-projects/instagram-logger

# Repopulate fake DB if needed (idempotent — re-runs reconcile cleanly):
IG_CLIENT=fake .venv/bin/python -m backend.ingest

# Start backend with fake-client auth state (so frontend doesn't show pre-login banner):
IG_CLIENT=fake .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# In another terminal, start frontend:
cd frontend && npm run dev
# Open http://localhost:5173/
```

**Note:** earlier we started uvicorn WITHOUT `IG_CLIENT=fake` and the frontend showed `NEEDS_FIRST_LOGIN`. Always export `IG_CLIENT=fake` before `uvicorn` until live login is unblocked.

## Next batch — B2 (Post detail modal)

Per plan §10 batch B2:
- Install `yet-another-react-lightbox` + plugins (Video, Captions, Counter, Thumbnails)
- Frontend: clicking a thumbnail opens YARL modal showing full carousel (multi-slide), inline `<video>` for video posts, caption + author + saved-at + which collections this post is in
- Verify on Safari: `<video>` seeking works (relies on `Range`/`Accept-Ranges` already implemented in `backend/api/media.py`)
- Verify thumbnails 304 on revisit (`If-None-Match` already implemented)
- Backend already returns full post detail via `GET /api/posts/:id` — no backend changes expected for B2 unless we discover a gap

## Locked decisions (do not re-debate, see plan §11)
- asyncio scheduler (not APScheduler), launchd deploy (not docker), `/api/media/:sha256` endpoint with full Range+304, in-process ingest with connection-per-thread + RLock, bind 127.0.0.1 unless `ALLOW_REMOTE=1`, WAL+NORMAL+`wal_checkpoint(TRUNCATE)` after each sync run, full M:M collections, keep-forever retention with `is_unsaved`/`is_source_deleted` flags, primary IG account, 24h ± 1h jitter

## Key files to read on resume
1. `.omc/plans/plan.md` — the spec; especially §10 (batches)
2. `.omc/research/critic-review.md` — the 14 must-fix items, all applied
3. `backend/ingest/run.py` — reconcile algorithm
4. `backend/ig_client/fake.py` (if it exists; otherwise the env-gated factory in `backend/ig_client/__init__.py`) — fake fixture client
5. `frontend/src/App.tsx` — current routing
