# Session handoff — instagram-logger

**Date paused:** 2026-05-07
**Status:** Code work for Phase 4 batch B2 is **complete and shipped**.
Real-IG ingest is **paused** because Instagram has fingerprint-flagged
the development MBP at the device level. Resume plan below.

---

## Phase status

| # | Phase | Status |
|---|---|---|
| P0 | Crystallize → brief | ✅ `.omc/plans/brief.md` |
| P1 | Research | ✅ `.omc/research/summary.md` |
| P2 | Plan + critic v2 | ✅ `.omc/plans/plan.md` (12 sections, 14 must-fix items applied) |
| P3 | Scaffold + vertical slice | ✅ |
| P4 | Iterative build | 🟡 B1 ✅, B2 ✅, B3+ blocked on auth |
| P5 | Verify & review | ⬜ |
| P6 | Ship | ⬜ |

---

## What was delivered 2026-05-06 → 2026-05-07

### B2 — Post detail modal
- `frontend/src/components/PostModal.tsx` using `yet-another-react-lightbox` with Counter + Video + Thumbnails plugins.
- Hash-routed overlay: `#/?p=<post_id>` and `#/collections/<id>?p=<post_id>` so back-button closes the modal cleanly.
- New `GET /api/posts/{post_id}` returning slides + caption + author + collections; `repo.get_post_detail()`.
- `PostGrid` cells became focusable `<button>`s with Enter/Space support.
- Verified end-to-end against the populated fake DB (40 posts, mixed image/carousel/video).

### IG best-practice client (instagrapi side)
- `IgClient` rewritten to follow the canonical instagrapi flow per docs:
  load_settings → login → dump_settings, with first-login-only device
  pinning (`set_country` / `set_country_code` / `set_locale` /
  `set_timezone_offset`) and `cl.delay_range = [1, 3]` jitter.
- New `IgClient.login_by_sessionid()` and `just import-session` CLI that
  bypasses `cl.login(user, pw)` entirely by injecting a Chrome-issued
  `sessionid` cookie. Probe replaced with `account_info()` (less
  redirect-prone than `get_timeline_feed`) and tolerates
  `TooManyRedirects` on the probe (saves anyway, lets sync exercise).
- New `IgClient.list_collections()` and `list_collection_items()` (real
  client was missing these — reconcile only worked under fake mode).
- Fixed `collection_medias_by_name` to use the modern instagrapi
  signature (`collection_pk_by_name` + `collection_medias(amount=...)`).

### Pacing knobs (intra-run)
- `IG_SYNC_PER_POST_DELAY_MIN/MAX` (default 1.0/3.0s).
- `IG_SYNC_PER_MEDIA_DELAY_MIN/MAX` (default 0.5/1.5s).
- `IG_SYNC_MAX_NEW_POSTS_PER_RUN` (default 50; trips `fully_enumerated=False` so the unsaved sweep doesn't fire on partial enumeration).
- All sleeps are no-ops when `IG_CLIENT=fake`.

### Auth state machine (frontend + backend)
- `repo.latest_sync_run()` reads the most recent sync_runs row.
- `/api/auth/status` now distinguishes `LOGGED_IN` /
  `SESSION_EXPIRED` (latest run state was `auth_required`) /
  `NEEDS_FIRST_LOGIN`.
- Frontend shows a non-blocking amber strip on `SESSION_EXPIRED`
  pointing at `just import-session`; cached posts stay browseable.
- `NEEDS_FIRST_LOGIN` empty-state copy now leads with
  `just import-session`.

### Web-API exploration (paused, kept on disk)
- `backend/ig_client/smoke_instaloader.py` — one-off probe that injects
  Chrome cookies (`sessionid` + optional `csrftoken`/`ds_user_id`/
  `mid`/`ig_did`) into instaloader and tries `Profile.get_saved_posts`.
  Sends `X-IG-App-ID: 936619743392459` + `Referer` headers.
- Result on this MBP: `403 Forbidden` from `graphql/query` even with all
  five cookies. IG's web API also rejects requests from this device.
- `instaloader` declared in `[project.optional-dependencies].web-fallback`.

### Repo
- Pushed to `git@github-personal:getarobo/instagram-logger.git` (the
  github-personal SSH alias maps to the getarobo personal account; the
  default `Host github.com` maps to sr-gene/work — see project memory).
- Initial commit "init: instagram-logger archiver, P3 + B1 + B2 + IG
  best-practice client" (57 files).

### Tests
- 8/8 pytest pass on fake fixture.
- frontend tsc clean.

---

## Auth blocker — current state (2026-05-07)

**Diagnosis (high confidence):** Instagram has device-fingerprint-flagged
the dev MBP. Confirmed by escalation timeline:

1. Day 1 — `BadPassword` from `just login` (account/device).
2. Day 1 — `BadPassword` from MBP over hotspot AND NordVPN (rules out IP-only).
3. Day 2 — Mobile app on phone works fine (no challenge); "send login link" emails arrive but link auth from MBP web fails too.
4. Day 2 — `cl.login_by_sessionid` succeeded → `account_info()` succeeded → `cl.collections()` returned `403 login_required` from the mobile API (web sessionid + mobile API headers reject).
5. Day 2 — instaloader cookie injection: `403 Forbidden` from web `graphql/query` even with all five cookies + correct headers.
6. Day 2 — Plain Chrome browsing on instagram.com showed an
   "automation warning" — heat from the day's probes leaked into
   normal browser fingerprint.

**Constants across all attempts:** the MBP. **Variables:** account
healthy, multiple IPs, both mobile and web APIs, both library and
direct browser. → device-level fingerprint flag, not IP, account, or
specific endpoint.

**What does NOT help (empirically validated):**
- VPN / hotspot / IP rotation
- Switching IG client library (instagrapi → instaloader same result)
- Cookie injection variants (sessionid alone, sessionid+csrftoken, all 5 cookies + correct headers)
- Password reset (didn't reach this; mobile app login worked without one)
- Mobile-app-issued login links

**What IS likely to help:**
- Time. IG device flags age off, typically 1–4 weeks if no further heat.
- A **different physical machine** (different fingerprint) for the
  password login bootstrap. Once `data/instagrapi_settings.json`
  exists with persisted UUIDs, the file can move back to MBP and
  subsequent runs reuse the trusted identity.
- A **Chrome extension** that runs in real browser context (the only
  approach indistinguishable from normal user activity).

---

## Resume plan

### Resume A — wait + retry from MBP (passive, $0)
1. **Stop running ANY IG-touching commands from this MBP for 7-14 days.**
   No `just login`, no `just import-session`, no
   `smoke_instaloader.py`, no automated probes of any kind. Even
   browsing instagram.com should be limited until the warning stops.
2. After 7 days, sanity-check by browsing instagram.com on Chrome MBP.
   No automation warning + saved posts grid renders → device flag is
   cooling. Try `just import-session` first (cheaper); if that fails,
   try `just login`.
3. After 14 days, retry the full flow. If still rejected, give up on
   this MBP and pursue B or C.

### Resume B — different machine (active, ~30 min if you have one)
1. Run `just install` on a different Mac (fresh fingerprint).
2. `just login` on that machine — give credentials, complete any IG
   challenge it presents.
3. Once `data/instagrapi_settings.json` exists, copy it back to the
   MBP repo, and `just sync` from MBP. The persisted UUIDs travel; IG
   sees the same trusted device that already passed challenges.

### Resume C — Chrome extension (active, ~2-3 days dev)
- Architecture sketch already brainstormed in conversation: extension
  scrapes saved-posts via real browser context; POSTs batches to a new
  `POST /api/ingest/extension` endpoint with a shared secret; existing
  reconcile algorithm runs unchanged.
- Right entry point: `/oh-my-claudecode:deep-interview` to scope the
  open design questions (scrape strategy, batching cadence, Manifest
  v3 service-worker shape, install flow, secret handling, recovery
  when IG schema drifts).

---

## Locked decisions (do not re-debate, plan §11)
asyncio scheduler (not APScheduler), launchd deploy (not docker),
`/api/media/:sha256` endpoint with full Range+304, in-process ingest
with connection-per-thread + RLock, bind 127.0.0.1 unless
`ALLOW_REMOTE=1`, WAL+NORMAL+`wal_checkpoint(TRUNCATE)` after each sync
run, full M:M collections, keep-forever retention with `is_unsaved`/
`is_source_deleted` flags, primary IG account, 24h ± 1h jitter.

---

## Resume commands

```bash
cd /Users/genehan/projects/claudehome-projects/instagram-logger

# Verify code still green:
.venv/bin/pytest tests/ -v
cd frontend && ./node_modules/.bin/tsc --noEmit && cd ..

# Run frontend + backend against fake fixture (always works):
IG_CLIENT=fake .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
# in another terminal:
cd frontend && npm run dev   # http://localhost:5173

# When auth flag has cooled (Resume A) or you have a fresh machine (B):
just login                    # password path, if it works
# OR
just import-session           # paste sessionid from Chrome cookies
.venv/bin/python -m backend.ingest    # full sync (real client, B1 reconcile)
```

---

## Key files / artifacts
1. `.omc/plans/plan.md` — the spec; especially §10 (batches), §11 (locked decisions).
2. `.omc/research/critic-review.md` — the 14 must-fix items (all applied).
3. `backend/ingest/reconcile.py` — full sync algorithm + pacing/cap.
4. `backend/ig_client/client.py` — instagrapi best-practice flow.
5. `backend/ig_client/import_session.py` — sessionid bypass CLI.
6. `backend/ig_client/smoke_instaloader.py` — web-API probe (paused).
7. `frontend/src/components/PostModal.tsx` — B2 modal.
8. Memory: `MEMORY.md` index in `.claude/projects/-Users-genehan-projects-claudehome-projects-instagram-logger/memory/`.
