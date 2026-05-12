# Extension pivot plan

**Date:** 2026-05-12 (revised after `/deep-interview` round 4)
**Spec:** `.omc/specs/deep-interview-extension-pivot.md` (ambiguity 9%, PASSED)
**Supersedes:** `plan.md` §3 (`backend/ig_client/`), §4 (sync/reconcile algorithm), §5 (re-auth UX), §10 batches B3–B6, §11 unsave/source-deleted retention semantics.
**Keeps:** `plan.md` §1 layout (partial), §2 schema base, §6 read APIs, §7 frontend (with additions), §11 locked decisions for DB/media/binding/launchd.

---

## 1. Goal & non-goals

**Goal:** archive saved IG posts via a Chrome extension running in the user's personal profile on the always-on Mac mini, indistinguishable from normal browsing. Existing FastAPI backend stores the data; existing React UI browses it.

**Non-goals (explicit):**
- ❌ No `instagrapi`, `instaloader`, or any HTTP-level IG API call from backend.
- ❌ No backend → IG CDN traffic. Bytes only enter the system via the extension.
- ❌ No headless Chrome / Puppeteer / Playwright. Real Chrome profile only.
- ❌ No automated login. User logs in manually, exactly once per session.
- ❌ No reconcile semantics. Archive is append-only after discovery. No `is_unsaved` / `is_source_deleted` flags. No full re-enumeration.
- ❌ No coexistence logic with human IG browsing in other tabs (extension runs on its jitter schedule regardless).
- ❌ No auto-retry beyond initial 3 attempts. Manual "Retry" button in the UI is the escape valve.

**North star:** stealth > speed > feature breadth. A 2-week initial crawl is fine. A 1-week crawl that gets the account banned is not.

---

## 2. Architecture

```
Mac mini (always on, user's Chrome profile)
│
├── Chrome (user's normal browsing happens here too)
│   ├── Tab N: instagram.com/<you>/saved/all-posts/   ← extension-driven, kept inactive
│   ├── Tab N+1: instagram.com/<you>/saved/<col>/     ← cycled per-collection
│   ├── Tab N+2: instagram.com/p/<shortcode>/         ← cycled per post (enrichment)
│   └── Other tabs: user's normal browsing            ← unrelated; no coordination
│
└── Extension (MV3)
    ├── service worker (background.ts): phase machine, alarms, dispatcher
    ├── content scripts:
    │   ├── saved-grid.ts   scrolls + extracts {shortcode, recency_rank} on /saved/*/
    │   ├── post-detail.ts  extracts full payload on /p/<shortcode>/
    │   └── auth-watch.ts   detects logout / login wall
    ├── offscreen.html      long-lived doc for media-fetch worker
    └── popup.html          manual controls: pause/resume/status/secret entry

         │  (POST / GET, header: X-Ingest-Secret: <shared>)
         ▼
FastAPI backend (localhost:8000, 127.0.0.1)
    ├── /api/ingest/extension/state         GET   resume cursor + phase + next_retry_target
    ├── /api/ingest/extension/collections   POST  list of collections
    ├── /api/ingest/extension/shortcodes    POST  batch of {shortcode, recency_rank}
    ├── /api/ingest/extension/membership    POST  shortcode → collection_id pairs
    ├── /api/ingest/extension/post          POST  full post payload (or 404 → state='lost')
    ├── /api/ingest/extension/media/exists  HEAD  sha256 dedup check
    ├── /api/ingest/extension/media         POST  multipart blob upload
    ├── /api/ingest/extension/media-failed  POST  signal a slide is unrecoverable
    ├── /api/ingest/extension/heartbeat     POST  liveness + session state + phase progress
    ├── /api/posts, /api/collections, /api/posts/:id  unchanged read endpoints
    ├── /api/posts/:id/retry-page           POST  user-triggered: reset to placeholder
    ├── /api/posts/:id/retry-media/:slide   POST  user-triggered: reset slide to pending
    └── /api/media/:sha256                  unchanged Range/304 endpoint

         │
         ▼
    SQLite (data/app.db, WAL) + data/media/<aa>/<sha>.<ext>

         │
         ▼
React frontend (localhost:5173)
    Normal tile · broken-image placeholder · tombstone tile · skeleton tile
    Each card has one "Retry" button that hits the contextually-correct endpoint.
```

---

## 3. Repo changes

### 3.1 Delete

- `backend/ig_client/` — entire directory (`client.py`, `fakes.py`, `login.py`, `import_session.py`, `smoke_instaloader.py`, `auth_state.py`).
- `backend/ingest/` — entire directory (`runner.py`, `reconcile.py`, `liveness.py`).
- `backend/scheduler/` — entire directory (`loop.py`).
- `backend/api/auth.py` and `/api/auth/*` routes from `backend/main.py`.
- `data/instagrapi_settings.json` (artifact only; already gitignored).
- Dependencies in `pyproject.toml`: `instagrapi`, `instaloader` and its `web-fallback` extra.
- `justfile` recipes: `login`, `sync`, `import-session`.
- Tests that exercise the deleted modules (`tests/unit/test_reconcile*`, `tests/integration/test_ingest*`, the fake-IG fixture loaders).
- Schema concepts in posts: `is_unsaved`, `is_source_deleted`, `last_seen_in_saved_at` deletion-sweep semantics (kept as nullable columns for compatibility but unused; migration 002 also drops them in the same step — see §5.3).
- Schema concepts in post_collections: `last_seen_at` deletion-sweep semantics (column stays nullable; sweep code removed).
- Schema concepts in sync_runs: `fully_enumerated` gating logic. Table stays for heartbeat history but no longer drives reconcile.

### 3.2 Keep (unchanged)

- `backend/db/` — schema, migrations, connection, repo (modulo column additions in §5.3).
- `backend/media/store.py` — atomic write + sha256 + ShortReadError is exactly what we need; it just gets fed `UploadFile.stream()` instead of `httpx.stream()`.
- `backend/api/posts.py`, `collections.py`, `media.py` — read endpoints stay; minor additions for `retry-page` / `retry-media` (could also live in `ingest_extension.py`; see §5.1).
- `backend/main.py` lifespan (minus scheduler start), `config.py` (127.0.0.1 bind rule stays).
- `frontend/` — all routes, components, queries (plus additions in §6).
- Tests for `repo`, `media/store`, `api/posts`, `api/collections`, `api/media`.

### 3.3 Add

**Backend:**
- `backend/api/ingest_extension.py` — the 9 ingest endpoints listed above + the 2 retry endpoints. Secret-gated via `X-Ingest-Secret` (constant-time compare).
- `backend/media/from_upload.py` — wraps starlette `UploadFile` into `media/store.py`'s atomic-write path. Re-hashes server-side, rejects sha mismatch.
- `backend/notify/telegram.py` — TODO stub. `alert(msg: str, severity: str = "warning") -> None` logs only; real Telegram wiring deferred to E8.
- `backend/ingest_state.py` — derives `{phase_suggestion, last_seen_recency_rank, total_discovered, total_enriched, total_lost, total_placeholder, next_enrichment_target, next_retry_target}` from existing tables.

**Extension (new top-level `extension/` directory):**
```
extension/
├── manifest.json           MV3
├── package.json
├── tsconfig.json
├── vite.config.ts          @crxjs/vite-plugin
├── src/
│   ├── background.ts       service worker: phase machine, alarms, dispatcher
│   ├── offscreen.html
│   ├── offscreen.ts        long-lived doc for media fetch + sha256 hashing
│   ├── content/
│   │   ├── saved-grid.ts   shortcode + recency_rank capture on /saved/*/
│   │   ├── post-detail.ts  payload extraction on /p/<shortcode>/
│   │   └── auth-watch.ts   logout detection on any IG page
│   ├── lib/
│   │   ├── api.ts          fetch wrapper, adds X-Ingest-Secret
│   │   ├── jitter.ts       awake-window scheduler, burst patterns, sleep helpers
│   │   ├── storage.ts      chrome.storage.local typed wrapper
│   │   ├── hash.ts         sha256 via SubtleCrypto
│   │   └── types.ts        shared types (Post, Slide, Collection, Phase, Heartbeat)
│   └── popup/
│       ├── popup.html
│       └── popup.tsx       status + pause/resume + secret entry
```

**Repo root:**
- `justfile` recipes: `ext-dev` (vite watch), `ext-build`, `ext-load` (prints instructions for loading unpacked).
- `.env.example` gains `INGEST_SECRET=`.

---

## 4. Extension design

### 4.1 Manifest (MV3)

```json
{
  "manifest_version": 3,
  "name": "instagram-logger",
  "version": "0.1.0",
  "permissions": ["storage", "alarms", "offscreen", "tabs", "scripting"],
  "host_permissions": [
    "https://www.instagram.com/*",
    "https://*.cdninstagram.com/*",
    "https://*.fbcdn.net/*",
    "http://127.0.0.1:8000/*"
  ],
  "background": { "service_worker": "background.js", "type": "module" },
  "content_scripts": [
    { "matches": ["https://www.instagram.com/*/saved/*"], "js": ["content/saved-grid.js"] },
    { "matches": ["https://www.instagram.com/p/*"], "js": ["content/post-detail.js"] },
    { "matches": ["https://www.instagram.com/*"], "js": ["content/auth-watch.js"], "run_at": "document_idle" }
  ],
  "action": { "default_popup": "popup.html" }
}
```

### 4.2 Phase machine (`background.ts`)

```
Phases:
  idle
  ↓
  discovery_all           scroll /saved/all-posts/ top→bottom, capture {shortcode, recency_rank}
  ↓
  discovery_collections   per-collection scroll, capture {shortcode, collection_id} pairs
  ↓
  enrichment              visit /p/<shortcode>/, oldest-first (ORDER BY recency_rank DESC)
  ↓
  watch                   top-peek /saved/all-posts/ every 12-24h jittered
  ↑       ↓
  paused  logged_out      manual pause from popup / auth-watch detects login wall
```

Stored state:
```ts
{
  phase: 'idle' | 'discovery_all' | 'discovery_collections' | 'enrichment' | 'watch' | 'paused' | 'logged_out',
  awake_window_start: '08:00',
  awake_window_end:   '01:00',
  rest_day_iso:       '2026-05-17',  // rotates weekly
  last_burst_at:      ISO,
  next_burst_at:      ISO,
  current_target:     { type: 'saved' | 'collection' | 'post', value: string } | null,
}
```

**Transitions:**
- `idle` → `discovery_all` if backend's `state.total_discovered == 0`; else resume backend's reported phase.
- `discovery_all` → `discovery_collections` when scroll-end detected (height stable across 5 attempts at 2–4s intervals).
- `discovery_collections` → `enrichment` when last collection's grid exhausted.
- `enrichment` → `watch` when `(enriched + lost) == total_discovered`. Posts in `placeholder` with `retry_count >= 3` are not blocking — they stay `placeholder` permanently until a manual retry.
- `watch` is steady-state. Every 12–24h jittered, runs a top-peek of `/saved/all-posts/` for the first 50 posts. New shortcodes → backend creates `placeholder` row → extension prepends to enrichment queue with elevated priority.
- Any phase → `logged_out` if auth-watch fires. Auth recovery is manual (user logs in); next heartbeat detects grid presence and restores previous phase.
- Any phase → `paused` via popup; resume restores previous phase.

### 4.3 Scheduler & jitter (`lib/jitter.ts`)

- **Awake window:** active only 08:00 ≤ now < 01:00 next-day, local tz. Outside: all alarms cleared.
- **Rest day:** one weekday per ISO week, chosen randomly at week boundary. Zero activity.
- **Bursts:** duration `uniform(180s, 900s)`. Inter-burst gap `uniform(30min, 180min)`.
- **Intra-burst:** scroll delay `uniform(800ms, 4000ms)`. Post-detail dwell `uniform(1500ms, 8000ms)`. Media spacing `uniform(400ms, 1800ms)`.
- **Phase multipliers:** discovery bursts can be longer (cheap per-event); enrichment bursts are shorter with longer gaps (heavier per-event).
- **All sleeps interruptible** by pause action or `logged_out` signal.
- **No coexistence detection.** Extension does not yield to human IG activity in other tabs.

### 4.4 Discovery (`content/saved-grid.ts`)

Two passes, same content script:

**Pass A — All Saved** (`/<you>/saved/all-posts/`):
- Scroll grid top → bottom; observe new `<a href="/p/<shortcode>/">` nodes via `MutationObserver`.
- Assign `recency_rank` from DOM order (0 = first-seen-at-top = newest).
- Batch every 50 shortcodes → `POST /api/ingest/extension/shortcodes` with `{source: 'all_posts', items: [{shortcode, recency_rank, thumb_url}]}`.
- End-of-list detection: scroll height stable for 5 consecutive attempts at 2–4s intervals.

**Pass B — Per collection** (`/<you>/saved/<collection-slug>/`):
- Same scroll pattern.
- Each batch tagged with `collection_id` (resolved from prior `/api/ingest/extension/collections` POST).
- POST → `/api/ingest/extension/membership` with `[{shortcode, collection_id}]`.
- Collection list is discovered at the start of Pass B by scraping `/<you>/saved/` (the index page).

### 4.5 Enrichment (`content/post-detail.ts`)

**Selection:** background polls `GET /api/ingest/extension/state` for `next_enrichment_target` — backend SQL is:

```sql
SELECT shortcode
FROM posts
WHERE state = 'placeholder'
  AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
ORDER BY recency_rank DESC   -- oldest first
LIMIT 1;
```

**Process per shortcode:**
1. Navigate dedicated post-detail tab to `/p/<shortcode>/`.
2. Wait for hydration (post DOM stable for 500ms, jittered up to 8s).
3. Detect outcome:
   - **404 / redirect to login / "Sorry, this page isn't available":** post is `lost`. POST `/api/ingest/extension/post` with `{shortcode, outcome: 'lost'}`. Backend sets `state='lost'`, `next_retry_at = now + 7 days` (one sanity re-check). After that re-check, no more auto-retries.
   - **Page rendered:** extract caption, author (username, full name, avatar URL, is_private), taken_at, media_kind, slides (`carousel_index`, `media_url`, `thumb_url`, `media_type`, dimensions, video duration), location, tagged users, raw HTML snippet. POST `/api/ingest/extension/post` with full payload. Backend upserts post, authors, posts_raw, post_media (slides with `state='pending'`). Backend sets `posts.state='enriched'`, `payload_fetched_at=now`.
4. For each slide's media URL: hand off to offscreen media-fetcher (§4.6).
5. Dwell jittered, then advance.

**Retry schedule for `placeholder` (transient failures: DOM-extraction failure, hydration timeout, network blip):**
- Try 1 fails → `retry_count=1`, `next_retry_at = now + 30min`.
- Try 2 fails → `retry_count=2`, `next_retry_at = now + 2h`.
- Try 3 fails → `retry_count=3`, `next_retry_at = NULL`. **Stop auto-retrying.** UI shows skeleton; user can click Retry.

A hard 404 jumps straight to `lost` (no transient retry curve). 7-day sanity recheck is one-shot.

### 4.6 Media fetcher (offscreen document)

Lives in `offscreen.ts` because MV3 service workers are evicted aggressively and can't hold Blobs reliably. Reachable via `chrome.runtime.sendMessage`.

```ts
async function fetchAndUpload(slide: SlideRef): Promise<MediaOutcome> {
  const resp = await fetch(slide.media_url, { credentials: 'include' });   // carries IG cookies
  if (!resp.ok) return { outcome: 'transient_fail', http: resp.status };

  const blob = await resp.blob();
  const sha = await sha256Hex(blob);

  const head = await fetch(`/api/ingest/extension/media/exists?sha=${sha}`, { method: 'HEAD' });
  if (head.status === 204) {
    await reportPresent(slide, sha);                                       // dedup hit
    return { outcome: 'present', sha, deduplicated: true };
  }

  const fd = new FormData();
  fd.append('file', blob, `${sha}.bin`);
  fd.append('sha256', sha);
  fd.append('mime', blob.type);
  fd.append('post_id', slide.post_id);
  fd.append('slide_idx', String(slide.slide_idx));
  await fetch('/api/ingest/extension/media', { method: 'POST', body: fd, headers: secretHeader() });
  return { outcome: 'present', sha, deduplicated: false };
}
```

**Rules:**
- **Single concurrency token across the whole extension.** Never more than one media in flight. Memory bounded to ~50MB worst case.
- Jittered delay between fetches (per §4.3).
- On `fetch` failure with CORS error: retry once with `mode: 'no-cors'` and ship the opaque Blob anyway — backend re-hashes from bytes.
- On second failure: count as `transient_fail` for that slide.

**Slide-level state machine:**

```
pending
  │
  ├─→ present              bytes on disk, sha256 row exists in media_files
  │
  └─→ media_failed         retries exhausted (3 with original URL + 2 with fresh URL after re-visit)
```

**Retry schedule for `pending` (media-level):**
- Tries 1–3 on the original URL, spaced 30min / 4h / 1d.
- If all 3 fail, schedule a **re-visit** of `/p/<shortcode>/` to capture fresh signed URLs (URLs expire). Backend signals re-visit by setting `post_media.last_url` and incrementing a `revisit_count`.
- Tries 4–5 with the fresh URL, spaced 30min / 1d.
- If still failing → `state='media_failed'`. Stop auto-retrying. UI shows broken-image placeholder; user can click Retry.

### 4.7 Auth watch (`content/auth-watch.ts`)

- Runs on every IG page (`document_idle`).
- Detects: redirect to `/accounts/login/`, presence of `<input name="username">` on `/saved/`, "Log in" banner, 401/403 on a saved-grid fetch.
- On detection: postMessage to background → `POST /api/ingest/extension/heartbeat {state: 'logged_out', at: ISO}` → backend calls `notify.telegram.alert(...)` (stub logs for v1).
- Extension transitions to `logged_out` phase; all alarms paused until next heartbeat detects a valid grid (user logged back in).

### 4.8 Storage (`chrome.storage.local`)

```ts
{
  secret: string,                           // entered once via popup
  phase: PhaseState,                        // see §4.2
  resume_cursor: {
    discovery_all:        { last_recency_rank: number | null, scroll_y: number },
    discovery_collections:{ current_collection_id: string | null, last_recency_rank: number | null },
    enrichment:           { last_shortcode_enriched: string | null },
  },
  burst_history: Array<{ start, end, posts_seen, media_uploaded }>,  // last 20 only
}
```

On boot: background calls `GET /api/ingest/extension/state` and reconciles with local storage. Backend wins on shortcode-existence and aggregate counts; local wins on intra-burst UI state.

### 4.9 Manual retry flow

User clicks "Retry" on a tile in the React UI:
- **Tombstone tile (`lost`):** UI calls `POST /api/posts/:id/retry-page`. Backend sets `state='placeholder'`, `retry_count=0`, `next_retry_at=now`. Backend pushes a one-shot signal in the next `/api/ingest/extension/state` response (field `priority_target: { shortcode, reason: 'manual_retry' }`). Extension prepends this shortcode to its enrichment queue.
- **Enriched tile with `media_failed` slide:** UI calls `POST /api/posts/:id/retry-media/:slide_idx`. Backend sets `post_media.state='pending'`, `retry_count=0`. Same priority-signal mechanism.

The Retry button shows a brief spinner; the user does not get a synchronous "succeeded/failed" result — the next render cycle of the tile reflects the new state when the extension's burst eventually processes it. (Bursts can be 30–180 min away. The UI conveys "queued" while waiting.)

---

## 5. Backend changes

### 5.1 New endpoints (`backend/api/ingest_extension.py`)

All ingest endpoints require `X-Ingest-Secret: <env INGEST_SECRET>` header (constant-time compare; mismatch → 401). Retry endpoints are NOT secret-gated (they're for the frontend on the same loopback origin; covered by `127.0.0.1` bind).

```
GET    /api/ingest/extension/state
       → { phase_suggestion, total_discovered, total_enriched, total_lost, total_placeholder,
            next_enrichment_target?: { shortcode },
            next_retry_target?:      { shortcode },          // surfaced after manual retry
            priority_target?:        { shortcode, reason },
            collections_known: [{ id, name, last_seen_at }],
            last_logged_out_at?: ISO }

POST   /api/ingest/extension/collections
       body: [{ id, name, is_all_posts }]
       → upserts; returns { ok: true }

POST   /api/ingest/extension/shortcodes
       body: { source: 'all_posts' | 'collection',
               collection_id?: string,
               items: [{ shortcode, recency_rank, thumb_url, position }] }
       → inserts placeholder posts rows (state='placeholder', recency_rank set)
         + upserts post_collections if collection_id present

POST   /api/ingest/extension/membership
       body: [{ shortcode, collection_id }]
       → upserts post_collections (last_seen_at = now, but no sweep)

POST   /api/ingest/extension/post
       body: { shortcode, outcome: 'enriched' | 'lost',
               (if enriched:) caption, taken_at, author: {...},
                              slides: [{ carousel_index, media_url, thumb_url, media_type,
                                          width, height, duration_seconds }],
                              raw_html_snippet }
       → if outcome='lost': sets posts.state='lost', next_retry_at = now + 7 days (one-shot)
         if outcome='enriched': upserts authors, posts (state='enriched', payload_fetched_at=now,
                                                        caption, taken_at, media_kind),
                                posts_raw, post_media (state='pending')

HEAD   /api/ingest/extension/media/exists?sha=<sha>
       → 204 if media_files row exists with that sha, else 404

POST   /api/ingest/extension/media
       multipart: file, sha256, mime, post_id, slide_idx
       → re-hash; reject on mismatch (400);
         write via media/from_upload.py (atomic);
         insert media_files row (sha PK; conflict = no-op);
         update post_media: media_sha256, state='present' for (post_id, slide_idx)

POST   /api/ingest/extension/media-failed
       body: { post_id, slide_idx, attempts, last_error }
       → updates post_media.retry_count; if exhausted, sets state='media_failed'

POST   /api/ingest/extension/heartbeat
       body: { state, phase, burst: {...}, last_error? }
       → updates ingest_meta;
         on state='logged_out' calls notify.telegram.alert(), rate-limited to once / 30min

POST   /api/posts/:id/retry-page             (loopback-only, not secret-gated)
       → posts.state='placeholder', retry_count=0, next_retry_at=now;
         sets ingest_meta.priority_target = (id, 'manual_retry_page')

POST   /api/posts/:id/retry-media/:slide_idx (loopback-only, not secret-gated)
       → post_media.state='pending', retry_count=0;
         sets ingest_meta.priority_target = (post_id, 'manual_retry_media')
```

### 5.2 Removed code

- `backend/api/auth.py` and `/api/auth/*` routes. Extension reports its own login state via heartbeat; frontend reads it from `/api/ingest/status` (rebadged subset of `/state`).
- `backend/scheduler/` — gone. No more scheduled IG calls; extension drives all cadence.
- `backend/ingest/runner.py`, `reconcile.py`, `liveness.py` — gone. The "reconcile" concept is deleted entirely.
- All references in `repo.py` to the unsave-flagging path and `fully_enumerated`-gated sweeps.

### 5.3 Schema additions / changes

**Migration `002_extension_state.sql`:**

```sql
-- posts: add recency_rank, state machine, retry tracking
ALTER TABLE posts ADD COLUMN recency_rank INTEGER;
ALTER TABLE posts ADD COLUMN state TEXT NOT NULL DEFAULT 'placeholder'
  CHECK (state IN ('placeholder', 'enriched', 'lost'));
ALTER TABLE posts ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN next_retry_at TEXT;
ALTER TABLE posts ADD COLUMN last_attempted_at TEXT;
ALTER TABLE posts ADD COLUMN payload_fetched_at TEXT;
CREATE INDEX idx_posts_state         ON posts(state);
CREATE INDEX idx_posts_recency       ON posts(recency_rank DESC);
CREATE INDEX idx_posts_next_retry    ON posts(next_retry_at)
  WHERE state IN ('placeholder', 'lost') AND next_retry_at IS NOT NULL;

-- post_media: add state machine, retry tracking, last_url for re-visit
ALTER TABLE post_media ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'
  CHECK (state IN ('pending', 'present', 'media_failed'));
ALTER TABLE post_media ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE post_media ADD COLUMN revisit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE post_media ADD COLUMN last_url TEXT;
ALTER TABLE post_media ADD COLUMN last_attempted_at TEXT;
CREATE INDEX idx_post_media_state ON post_media(state);

-- Note: legacy columns posts.is_unsaved, posts.is_source_deleted,
-- posts.last_seen_in_saved_at, post_collections.last_seen_at remain nullable
-- but are no longer maintained. Application code never reads or writes them.
-- A future migration can DROP them once we're sure nothing depends on them.

INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'));
```

**Migration `003_ingest_meta.sql`:**

```sql
CREATE TABLE ingest_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_heartbeat_at  TEXT,
  last_phase         TEXT,
  last_logged_out_at TEXT,
  last_alert_at      TEXT,        -- for telegram rate-limit
  priority_target_post_id TEXT,   -- one-shot from manual retry
  priority_target_reason  TEXT
);
INSERT INTO ingest_meta(id) VALUES (1);

INSERT INTO schema_migrations(version, applied_at) VALUES (3, datetime('now'));
```

---

## 6. Frontend changes

### 6.1 Components

- **Drop** `AuthBanner` (the "session expired" amber strip from the old plan).
- **Add** `IngestStatusCard` reading `/api/ingest/status` (subset of `/state`): phase, last burst, total counts, last heartbeat, optional logged-out warning.
- **Add** `IngestPage` (sibling to `Sync` page that no longer exists): phase machine, recent bursts table, retry-pending lists.
- **Tile states** in `PostThumb` / `PostGrid`:
  - `state='enriched'` AND all `post_media.state='present'` → normal tile.
  - `state='enriched'` AND any `post_media.state='media_failed'` → tile with broken-image placeholder over failed slides; "Retry" button overlay on hover.
  - `state='lost'` → greyed tombstone tile with author username + "Last seen DDD ago" + "Retry" button. Shortcode + thumb_url shown if available.
  - `state='placeholder'` → skeleton tile (Tailwind `animate-pulse`).
- **Retry button:** one per tile. Calls either `/retry-page` or `/retry-media/:slide_idx` depending on context. Optimistic UI: tile shows "Queued" badge until next render cycle pulls fresh state.

### 6.2 Query keys

Add to existing TanStack Query setup:
```
['ingest','status']              poll 30s
['ingest','phase']               poll 60s
```

Existing `['posts', ...]`, `['post', id]`, `['collections']` are unchanged.

---

## 7. Telegram stub

`backend/notify/telegram.py`:

```python
import logging
log = logging.getLogger(__name__)

# TODO(2026-05-12, gated on E8): replace with real Telegram Bot API send.
# Env vars (future): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
# httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})
def alert(message: str, *, severity: str = "warning") -> None:
    log.warning("[TELEGRAM TODO severity=%s] %s", severity, message)
```

Called from `/api/ingest/extension/heartbeat` when `state == 'logged_out'`. Rate-limited to once per 30min via `ingest_meta.last_alert_at`.

---

## 8. Stealth posture (the rules)

Hard rules baked into extension code:

1. **Never automate login.** No password autofill, no challenge solving, no cookie injection.
2. **Real Chrome user agent.** Never override.
3. **Reuse tabs.** One dedicated saved-grid tab; one dedicated post-detail tab; one dedicated collection tab. No rapid open/close.
4. **Never set scraper tabs `active: true`.** They stay in the background. If user manually focuses one, extension takes no special action (no coexistence logic per round 2 decision) — but the tab being foregrounded does not change behavior either.
5. **Honor `chrome.idle.queryState`.** Skipped in the current design (no coexistence logic). Listed here as a deliberate omission.
6. **No XHR/fetch to IG's GraphQL endpoints.** Only fetches the page itself makes naturally (= media CDN URLs the page already references). Page-driven only.
7. **No exotic DOM manipulation.** Scroll = `window.scrollTo`. Clicks = `dispatchEvent(MouseEvent)`. Nothing a user couldn't do.
8. **Awake-window + bursts + rest day** per §4.3.
9. **`logged_out` halts everything.** No silent retries that pile heat.
10. **One media at a time, globally.** Concurrency token in `background.ts`.
11. **No automatic retry beyond initial 3.** Manual retry button is the only escape valve.

---

## 9. Verification plan

Hard to test against real IG without re-tripping the device fingerprint flag. So:

**Backend tests (CI-safe):**
- Endpoint contract tests for all 11 new endpoints (FastAPI TestClient).
- Multipart media upload: success, sha mismatch (400), dedup HEAD (204), atomic-write integrity.
- State derivation: given a DB state, `state` endpoint returns the expected phase suggestion and `next_enrichment_target`.
- Heartbeat: `logged_out` calls `notify.alert()` exactly once per 30min window.
- Retry endpoints: state transitions and `priority_target` mechanics.
- Migration 002 + 003: apply against a copy of the existing dev DB, verify no data loss.

**Extension tests (local-only, fake IG):**
- Stand up `tests/fixtures/fake-ig/` static HTML site mimicking `/saved/all-posts/`, `/saved/<col>/`, `/p/<shortcode>/`, with a few intentional 404s and one media URL that 403s.
- Serve via `python -m http.server`; build extension with `ext-dev` flag that adds `localhost` to host_permissions.
- Manual smoke: load extension, point at fake IG, watch it cycle through discovery → enrichment → media upload → watch. Assert backend has expected counts + at least one tombstone + one `media_failed` slide. Click Retry; observe queue advancement.

**Real-IG verification (manual, one-time, gated on Resume A cooloff per `SESSION_HANDOFF.md`):**
- After 7–14 day device-flag cooloff window: user logs in manually in Chrome → extension picks up session.
- Let `discovery_all` run for ~30 min in a single burst; verify backend has expected count of placeholder rows.
- Pause, check DB state, check that no IG anti-automation warning appears in normal browsing.
- If clean: resume; let it run to completion over 1–2 weeks.

---

## 10. Migration / batch ordering

### E0 — Repo cleanup (½ day)
- Delete `ig_client/`, `ingest/`, `scheduler/`, `api/auth.py`.
- Strip `instagrapi`, `instaloader` from `pyproject.toml`.
- Remove `just login`, `just sync`, `just import-session`.
- Drop tests that referenced removed modules.
- `pytest` green, `ruff check` clean, frontend `tsc` clean (auth-banner stubbed).

### E1 — Backend ingestion + retry endpoints (1–2 days)
- Migrations `002_extension_state.sql`, `003_ingest_meta.sql`.
- `backend/api/ingest_extension.py` with all 11 endpoints, secret-gated where applicable.
- `backend/media/from_upload.py`.
- `backend/notify/telegram.py` stub.
- Tests: every endpoint, every error path.

### E2 — Extension skeleton (1–2 days)
- `extension/` scaffolding: manifest, @crxjs/vite-plugin build, popup with secret entry.
- `lib/api.ts`, `lib/storage.ts`, `lib/jitter.ts`, `lib/hash.ts`.
- `background.ts` phase machine with `idle` only (no scraping yet).
- Popup shows backend `/state` response. Smoke: load extension, paste secret, round-trip.

### E3 — Discovery (Pass A + Pass B) with recency_rank (2–3 days)
- `content/saved-grid.ts` + collection enumeration.
- Phase transitions `discovery_all` → `discovery_collections` → idle.
- Verify against fake-IG fixture.

### E4 — Enrichment + media fetch + state machines (3–4 days)
- `content/post-detail.ts` + offscreen media worker.
- Post-state machine (placeholder → enriched | lost) + media-state machine (pending → present | media_failed).
- Oldest-first ordering via `recency_rank DESC`.
- Backend post + media upload flows fully wired.
- Verify against fake-IG fixture including a video carousel + one intentional 404 + one bad-media URL.

### E5 — Watch + auth-watch + Telegram stub (1 day)
- `content/auth-watch.ts` + heartbeat + `notify.alert` stub.
- `watch` phase loop (top-peek every 12–24h).

### E6 — Frontend tiles + retry buttons (1 day)
- Tombstone tile, broken-image placeholder, skeleton tile, single "Retry" button.
- `IngestStatusCard`, new `IngestPage`.

### E7 — Real-IG smoke (when safe per Resume A) (1 day attended)
- See §9.

### E8 — Telegram real wiring (later, gated on E7) (½ day)
- Replace stub with real Telegram Bot API call.
- Add `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` to `.env.example`.

---

## 11. Open questions / TODOs

- **Media fetch CORS:** confirm at E4 time whether IG CDN URLs are fetchable with `credentials: 'include'` from extension content-script context, or whether we need `mode: 'no-cors'` opaque-blob fallback. Either works; affects only the response-inspection path.
- **End-of-list detection on /saved/:** confirm IG still uses the "scroll-height-stops-growing" pattern (vs. an explicit "you've reached the end" sentinel). Empirically verify during E3.
- **Tab management granularity:** plan currently uses three dedicated tabs (saved-grid, collection-grid, post-detail). Confirm at E2 whether one shared tab with URL transitions is less suspicious than three persistent tabs.
- **Rest-day pattern:** uniform random weekday vs. weighted (more rests on Sun/Mon)? Default uniform; revisit if patterns look bot-like.
- **Video extraction edge cases:** IG sometimes serves DASH/HLS for longer videos. Spike at E4 to determine: detect manifest URL → ship manifest + segments OR fall back to MediaRecorder OR mark `media_failed` for now. v1 acceptable to skip DASH videos with `media_failed`; user can retry later.
- **Account flag from old code paths:** confirm at end of E0 that no remaining code imports from deleted modules (`grep -r ig_client backend/ tests/`).
- **Re-visit cadence for `lost` 7-day sanity:** is one shot at 7 days enough, or should there be a series (7d, 30d, 90d)? Default one shot.

---

## 12. Locked decisions (do not re-debate)

1. **Media via extension `fetch()`**, not backend HTTP to IG CDN. (§4.6)
2. **One media at a time** in the extension. (§4.6, §8)
3. **Awake-window + bursts + rest day**, not 24/7 steady. (§4.3, §8)
4. **No coexistence logic** with human IG browsing in other tabs. (§4.3, §8 rule 5 omission)
5. **Discovery = Pass A (all saved) + Pass B (per collection)**, joined via existing `post_collections`. (§4.4)
6. **Capture `recency_rank` at discovery time; enrichment proceeds oldest-first (`ORDER BY recency_rank DESC`).** (§4.4, §4.5)
7. **No reconcile semantics. Archive is append-only after discovery.** `is_unsaved` / `is_source_deleted` flags retired. (§1, §5.3)
8. **Watch mode = top-peek every 12–24h only.** No full re-enumeration. (§4.2)
9. **Post state machine: `placeholder → enriched | lost`** with 3 auto-retries + hard 404 → immediate `lost`. (§4.5)
10. **Media state machine: `pending → present | media_failed`** with 3 + 2 (post-revisit) retries. (§4.6)
11. **No auto-retry beyond initial 3 attempts.** Manual UI Retry button is the escape valve. Tombstones and broken-image placeholders are first-class UI states. (§4.9, §6)
12. **No automated login.** Manual login only; Telegram alert (stubbed in v1) on session loss. (§4.7, §8)
13. **Extension lives in the user's personal Chrome profile**, not a dedicated Chrome instance. (§2, §8)
14. **Backend keeps 127.0.0.1 bind**, secret-gated ingest endpoints, retry endpoints loopback-only without secret. (§5.1)
