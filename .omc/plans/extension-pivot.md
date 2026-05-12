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
- `.env.example` gains:
  - `INGEST_SECRET=change-me-32-byte-hex`
  - `MAX_MEDIA_GB=50` (consensus R7: storage exhaustion guard threshold)

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
  ↑       ↓     ↓
  paused  logged_out  throttling_suspected   manual pause / auth-watch fires / heat-watch fires (consensus R6)
```

**Tab-ownership invariants (per consensus Δ4):**
- Every tab opened by the SW (`chrome.tabs.create`) is immediately registered in `chrome.storage.local.extension_owned_tabs[tabId] = {role, createdAt}`.
- On SW resume, the SW iterates this map (NEVER `chrome.tabs.query` against `https://www.instagram.com/*` at large). For each entry: `chrome.tabs.get(id)` confirms existence and URL-matches-role; missing or drifted tabs are pruned and recreated.
- User-opened IG tabs are invisible to the extension — they are never targeted, never read, never modified.
- This prevents the extension from hijacking a tab the user opened manually for browsing.

Stored state:
```ts
{
  phase: 'idle' | 'discovery_all' | 'discovery_collections' | 'enrichment'
        | 'watch' | 'paused' | 'logged_out' | 'throttling_suspected',
  awake_window_start: '08:00',
  awake_window_end:   '01:00',
  rest_day_iso:       '2026-05-17',  // rotates weekly
  last_burst_at:      ISO,
  next_burst_at:      ISO,
  current_target:     { type: 'saved' | 'collection' | 'post', value: string } | null,
  extension_owned_tabs: Record<number, { role: 'saved-grid' | 'post-detail' | 'collection', createdAt: ISO }>,
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

**Phase precedence (when multiple alerts pending)**

When multiple alert conditions are pending simultaneously, the active phase resolves by this precedence (highest wins): `logged_out > storage_low > throttling_suspected > paused > active phases (idle | discovery_all | discovery_collections | enrichment | watch)`. Individual `last_*_at` timestamps in `ingest_meta` are preserved independently for forensic readability. A higher-precedence heartbeat overwrites a lower-precedence one in `ingest_meta.last_phase`; arriving in opposite order, the lower-precedence heartbeat is logged but does not change `last_phase`.

### 4.3 Scheduler & jitter (`lib/jitter.ts`)

- **Awake window:** active only 08:00 ≤ now < 01:00 next-day, local tz. Outside: all alarms cleared.
- **Rest day:** one weekday per ISO week, chosen randomly at week boundary. Zero activity.
- **Bursts:** duration `uniform(180s, 900s)`. Inter-burst gap `uniform(30min, 180min)`.
- **Intra-burst:** scroll delay `uniform(800ms, 4000ms)`. Post-detail dwell `uniform(1500ms, 8000ms)`. Media spacing `uniform(400ms, 1800ms)`.
- **Phase multipliers:** discovery bursts can be longer (cheap per-event); enrichment bursts are shorter with longer gaps (heavier per-event).
- **All sleeps interruptible** by pause action or `logged_out` signal.
- **No coexistence detection.** Extension does not yield to human IG activity in other tabs.

**Per-burst metrics capture (consensus R6 / AC#21):**
At burst close, append a metrics record to `chrome.storage.local.burst_metrics` (rolling 7-burst window, oldest record trimmed on insert):

```ts
chrome.storage.local.burst_metrics: Array<{
  burst_id: string,
  closed_at: ISO,
  hydration_p50_ms: number,        // median /p/<shortcode>/ DOM-stable time within the burst
  http_4xx_rate:    number,        // 4xx responses / total media fetches in the burst (0..1)
  login_redirects:  number,        // count of mid-burst redirects to /accounts/login/
  posts_seen:       number,
  media_uploaded:   number,
}>  // length capped at 7
```

After each new record is appended, the scheduler evaluates the three R6 triggers against the rolling baseline (mean of the prior 7-burst window, excluding the just-closed burst). If ANY trigger fires:
- (a) `hydration_p50_ms > 1.5 × baseline.hydration_p50_ms`
- (b) `http_4xx_rate > 0.05` AND `baseline.http_4xx_rate < 0.02`
- (c) `login_redirects > 0` (mid-burst, not at session start)

…the SW fires a `state='throttling_suspected'` heartbeat carrying the metrics payload (see §4.7) and self-transitions to the `paused` phase. Resume requires manual ack via the popup.

Trigger evaluation runs only after 4+ bursts have populated the baseline (avoids false positives during warm-up).

**Test-mode bypass:** when `chrome.storage.local.test_mode_skip_warmup === true`, trigger evaluation runs from burst 1 with the burst-0 baseline assumed to be `{hydration_p50_ms: 0, http_4xx_rate: 0, login_redirects: 0}`. Production builds default this flag to `false`; the fake-IG fixture sets it to `true` so AC#21 smoke can fire on a single bad burst without seeding 4 fake good bursts first.

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
- On detection: postMessage to background → `POST /api/ingest/extension/heartbeat {state: 'logged_out', at: ISO}` → backend calls `notify.telegram.alert(...)` (stub appends JSONL to `.omc/logs/alerts.log` per consensus Δ7).
- Extension transitions to `logged_out` phase; all alarms paused until next heartbeat detects a valid grid (user logged back in).

**Heartbeat schema (consensus R6 / AC#21 — adds `metrics`):**

```ts
POST /api/ingest/extension/heartbeat
body: {
  state: 'ok' | 'logged_out' | 'throttling_suspected' | 'selectors_broken' | 'extraction_failed' | 'storage_low',
  phase: PhaseState['phase'],
  burst?: { id, started_at, closed_at, posts_seen, media_uploaded },
  metrics?: {                                 // present when state ∈ {throttling_suspected, ok-with-trailing-window}
    hydration_p50_ms: number,
    http_4xx_rate:    number,                 // 0..1
    login_redirects:  number,
  },
  last_error?: string,
}
```

Note: when extension is in `phase='paused'` via popup pause/resume, heartbeat carries `state: 'ok'` with `phase: 'paused'`. The `state` field captures unhealthy conditions; phase captures the operational mode.

Backend behavior:
- `state='logged_out'`: write `ingest_meta.last_logged_out_at = now`; call `notify.telegram.alert(...)` severity=`critical`.
- `state='throttling_suspected'`: write `ingest_meta.last_throttling_at = now` + persist the `metrics` payload to `ingest_meta.last_throttling_metrics_json`; next `/state` response sets `phase='paused'`; call `notify.telegram.alert(...)` severity=`critical`.
- `state='storage_low'`: write `ingest_meta.last_storage_low_at = now`; next `/state` response sets `phase='paused'`; call `notify.telegram.alert(...)` severity=`critical`.
- All alert paths are rate-limited to once / 30min via `ingest_meta.last_alert_at` to avoid duplicate JSONL spam.

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

The Retry button shows a brief spinner; the user does not get a synchronous "succeeded/failed" result — the next render cycle of the tile reflects the new state when the extension's burst eventually processes it. Per consensus Δ2, when `priority_target` is set the SW schedules an early burst within `uniform(60s, 300s)` (capped at one early-burst per 30min window). The UI conveys "Queued — running in ~Xm" while waiting.

**Logged-out UX (consensus Δ6 / T3):** When the latest `/api/ingest/status` reports `phase === 'logged_out'`:
- The per-tile Retry button renders with `disabled` attribute.
- A hover `title` tooltip reads: "Extension is logged out of Instagram. Log in via Chrome to resume retries."
- The button auto-re-enables within ~30s of the user logging back in (via the existing `['ingest','status']` TanStack Query poll cadence; auth-watch fires a heartbeat with `state='ok'` once it detects a valid grid).
- No new click handler, no separate state machine — the React component already reads `status.phase` for banner rendering, the button just adds `disabled={status?.phase === 'logged_out'}` to the existing JSX.

The same `disabled` rule applies when `phase === 'throttling_suspected'` or `phase === 'storage_low'` (paused states from R6/R7); the tooltip text adapts to the cause.

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

**Migration `002_extension_state.sql`** (wrapped in transaction per consensus Δ1; adds materialized aggregate columns + triggers per consensus Δ3):

```sql
-- CHECK applies to future writes only; existing rows accept the DEFAULT,
-- which is in the allowed set. Verified against SQLite 3.51.0 on dev.

PRAGMA foreign_keys = ON;
BEGIN;

-- posts: add recency_rank, state machine, retry tracking
ALTER TABLE posts ADD COLUMN recency_rank INTEGER;
ALTER TABLE posts ADD COLUMN state TEXT NOT NULL DEFAULT 'placeholder'
  CHECK (state IN ('placeholder', 'enriched', 'lost'));
ALTER TABLE posts ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN next_retry_at TEXT;
ALTER TABLE posts ADD COLUMN last_attempted_at TEXT;
ALTER TABLE posts ADD COLUMN payload_fetched_at TEXT;

-- consensus Δ3 (option b): materialized aggregate columns for tile rendering.
-- Maintained by triggers below; replaces correlated subqueries in /api/posts.
ALTER TABLE posts ADD COLUMN slides_total   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN slides_present INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN slides_failed  INTEGER NOT NULL DEFAULT 0;

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

-- consensus Δ3 (option b): triggers maintain posts.slides_{total,present,failed}.
-- Mirrors the FTS trigger pattern already used in 001_init.sql.
CREATE TRIGGER post_media_aggr_ins AFTER INSERT ON post_media
BEGIN
  UPDATE posts SET
    slides_total   = slides_total + 1,
    slides_present = slides_present + (NEW.state = 'present'),
    slides_failed  = slides_failed  + (NEW.state = 'media_failed')
  WHERE id = NEW.post_id;
END;

CREATE TRIGGER post_media_aggr_upd AFTER UPDATE OF state ON post_media
BEGIN
  UPDATE posts SET
    slides_present = slides_present + (NEW.state = 'present')     - (OLD.state = 'present'),
    slides_failed  = slides_failed  + (NEW.state = 'media_failed') - (OLD.state = 'media_failed')
  WHERE id = NEW.post_id;
END;

CREATE TRIGGER post_media_aggr_del AFTER DELETE ON post_media
BEGIN
  UPDATE posts SET
    slides_total   = slides_total   - 1,
    slides_present = slides_present - (OLD.state = 'present'),
    slides_failed  = slides_failed  - (OLD.state = 'media_failed')
  WHERE id = OLD.post_id;
END;

-- Note: legacy columns posts.is_unsaved, posts.is_source_deleted,
-- posts.last_seen_in_saved_at, post_collections.last_seen_at remain nullable
-- but are no longer maintained. Application code never reads or writes them.
-- A future migration can DROP them once we're sure nothing depends on them.

INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'));

COMMIT;
```

Integration test (per consensus §5.6): apply migration against a copy of `data/app.db`; assert no row loss; assert new columns + triggers exist; assert CHECK enforced on bad INSERT; **assert atomicity** by monkeypatching `cursor.execute` to raise on the third statement and verifying rollback leaves the schema unchanged.

**Migration `003_ingest_meta.sql`** (wrapped in transaction; adds R6/R7 fields per consensus AC#21/AC#22):

```sql
PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE ingest_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_heartbeat_at         TEXT,
  last_phase                TEXT,
  last_logged_out_at        TEXT,
  last_throttling_at        TEXT,        -- consensus R6: heat detection
  last_throttling_metrics_json TEXT,     -- consensus R6: snapshot of breaching burst
  last_storage_low_at       TEXT,        -- consensus R7: storage exhaustion
  last_alert_at             TEXT,        -- telegram rate-limit (any severity=critical alert)
  layout_warning_at         TEXT,        -- R5: selector breakage canary
  priority_target_post_id   TEXT,        -- one-shot from manual retry
  priority_target_reason    TEXT
);
INSERT INTO ingest_meta(id) VALUES (1);

INSERT INTO schema_migrations(version, applied_at) VALUES (3, datetime('now'));

COMMIT;
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

## 7. Telegram stub + JSONL alert persistence (consensus Δ7 / R8)

`backend/notify/telegram.py`:

```python
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
ALERTS_LOG = Path(".omc/logs/alerts.log")

# TODO(2026-05-12, gated on E8): replace the log line below with a real
# Telegram Bot API send. The JSONL append MUST stay regardless — it is the
# forensic record between E5 (alerts start firing) and E8 (Telegram is live).
# Env vars (future): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
# httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})
def alert(message: str, *, severity: str = "warning") -> None:
    ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "message": message,
    }
    with ALERTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    log.warning("[TELEGRAM TODO severity=%s] %s", severity, message)
```

Called from `/api/ingest/extension/heartbeat` when `state ∈ {'logged_out', 'throttling_suspected', 'storage_low', 'selectors_broken', 'extraction_failed'}` with severity=`critical` (or `warning` for layout-only canaries). Rate-limited to once per 30min per severity-bucket via `ingest_meta.last_alert_at`.

**Frontend wiring (consensus Δ7):** `IngestStatusCard` reads the following fields from `/api/ingest/status`:
- `last_logged_out_at` → red banner: "Log in to Instagram in Chrome to resume."
- `last_throttling_at` (+ `last_throttling_metrics_json`) → red banner: "Pace adjusted — burst metrics breached baseline at HH:MM."
- `last_storage_low_at` → red banner: "Media disk at >80% of `MAX_MEDIA_GB`; enrichment paused."
- `layout_warning_at` → amber banner: "IG layout change detected; manual ack required."

Banners persist regardless of Telegram wiring status (E5 → E8 window is observable without Telegram).

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
- Migration runner applies migrations in version order: 002 before 003. Heartbeat handler in `ingest_extension.py` MUST guard against `ingest_meta` row absence (defensive read) in case 003 hasn't applied yet during partial-deploy scenarios.

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
- **Tab management granularity:** plan currently uses three dedicated tabs (saved-grid, collection-grid, post-detail). Confirm at E2 whether one shared tab with URL transitions is less suspicious than three persistent tabs. Tracked as consensus O3.
- **Rest-day pattern:** uniform random weekday vs. weighted (more rests on Sun/Mon)? Default uniform; revisit if patterns look bot-like. Tracked as consensus O4.
- **Video extraction edge cases:** IG sometimes serves DASH/HLS for longer videos. Spike at E4 to determine: detect manifest URL → ship manifest + segments OR fall back to MediaRecorder OR mark `media_failed` for now. v1 acceptable to skip DASH videos with `media_failed`; user can retry later. Tracked as consensus O5.
- **Account flag from old code paths:** confirm at end of E0 that no remaining code imports from deleted modules (`grep -r ig_client backend/ tests/`).
- **Re-visit cadence for `lost` 7-day sanity:** is one shot at 7 days enough, or should there be a series (7d, 30d, 90d)? Default one shot.
- **(Consensus O1) DYI pre-seed for very large backlogs:** v1 chose option (a) "widen timeline to 1–6 weeks" rather than option (b) "add E3.5 DYI pre-seed endpoint". Re-evaluate for v2 if real-world saved-post count is empirically > 7000 OR if user explicitly accepts the engineering cost of a Meta DYI JSON-schema parser + new `POST /api/ingest/dyi-import` endpoint.
- **(Consensus O2) Single-concurrency token relaxation:** re-evaluate at v1 → v1.1 if throughput at scale proves intolerable AND no heat signals (R6) have fired in 30 days at locked pacing.
- **(Consensus R6 tuning) Heat-detection thresholds:** initial thresholds (1.5× hydration baseline, >5% 4xx with <2% baseline, mid-burst login redirect) are conservative. Re-tune at end of E7 based on real-IG burst telemetry from the first week of operation.

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
