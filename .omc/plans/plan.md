# Implementation plan (v2 — post-critic)

## 1. Repo layout

```
instagram-logger/
├── backend/
│   ├── main.py                  # FastAPI factory; uvicorn lifespan starts scheduler
│   ├── config.py                # pydantic-settings; bind=127.0.0.1 default; ALLOW_REMOTE gate
│   ├── api/{posts,collections,sync,media,auth,deps}.py
│   ├── db/
│   │   ├── connection.py        # per-thread sqlite3 (check_same_thread=False) + RLock
│   │   ├── migrations/001_init.sql … NNN_*.sql
│   │   └── repo.py              # only place SQL strings live
│   ├── ingest/{runner,reconcile,liveness}.py
│   ├── ig_client/{client,auth_state,session,fakes}.py
│   ├── media/{store,downloader}.py
│   └── scheduler/loop.py        # asyncio sleep loop; dormant in NEEDS_FIRST_LOGIN
├── frontend/                    # Vite 6 + React 19 + TS
├── data/                        # gitignored
│   ├── app.db                   # WAL + synchronous=NORMAL
│   ├── media/<aa>/<sha256>.<ext>
│   ├── media/.tmp/              # in-flight downloads only; never served
│   ├── thumbnails/<aa>/<sha256>.jpg
│   ├── backups/                 # excluded from tmutil
│   ├── instagrapi_settings.json
│   ├── scheduler.state          # paused flag
│   ├── .metadata_never_index
│   └── logs/{stdout.log,stderr.log}
├── ops/com.genehan.iglogger.plist
├── tests/{unit,integration,fixtures}
├── pyproject.toml               # fastapi, instagrapi, uvicorn, pydantic-settings, httpx, pytest, vcrpy
├── justfile                     # dev, test, sync-once, backup, install-launchd, uninstall-launchd
├── .env.example                 # DATA_DIR, HOST=127.0.0.1, PORT, ALLOW_REMOTE=0
└── README.md
```

**`config.py` bind rule (locked):** `HOST` defaults to `127.0.0.1`. If `HOST != "127.0.0.1"`, server refuses to start unless `ALLOW_REMOTE=1`. Enforced in `main.py` startup before uvicorn binds.

**launchd (locked):** `ops/com.genehan.iglogger.plist` ships with `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=60`, `StandardOutPath=<DATA_DIR>/logs/stdout.log`, `StandardErrorPath=<DATA_DIR>/logs/stderr.log`, and `EnvironmentVariables` for `DATA_DIR`, `HOST=127.0.0.1`, `PORT`, optional `ALLOW_REMOTE`. `just install-launchd` copies plist + drops `.metadata_never_index` + runs `tmutil addexclusion data/media`. `just backup` runs `sqlite3 app.db ".backup data/backups/app-<ts>.db"` (WAL-safe); `data/backups/` is excluded from Time Machine.

---

## 2. Database schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE authors (
  id            TEXT PRIMARY KEY,
  username      TEXT NOT NULL,
  full_name     TEXT,
  is_private    INTEGER NOT NULL DEFAULT 0,
  profile_pic_url TEXT,                    -- nullable URL; no media FK in v1
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);
CREATE INDEX idx_authors_username ON authors(username);

CREATE TABLE posts (
  id                     TEXT PRIMARY KEY,
  shortcode              TEXT NOT NULL UNIQUE,
  author_id              TEXT NOT NULL REFERENCES authors(id),
  author_username_denorm TEXT NOT NULL,    -- kept in sync via trigger; cheap grid joins
  caption                TEXT,
  media_kind             TEXT NOT NULL,    -- 'image'|'video'|'carousel'
  taken_at               TEXT,
  saved_at               TEXT,             -- nullable; IG inconsistent
  first_seen_at          TEXT NOT NULL,    -- write-once; never updated on upsert
  last_seen_in_saved_at  TEXT NOT NULL,
  is_unsaved             INTEGER NOT NULL DEFAULT 0,
  is_source_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_posts_saved_at  ON posts(COALESCE(saved_at, first_seen_at) DESC, id DESC);
CREATE INDEX idx_posts_last_seen ON posts(last_seen_in_saved_at DESC);
CREATE INDEX idx_posts_author    ON posts(author_id);
CREATE INDEX idx_posts_unsaved   ON posts(is_unsaved);

CREATE TABLE posts_raw (              -- moved out of posts to keep row-size small
  post_id TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  json    TEXT NOT NULL
);

CREATE VIRTUAL TABLE posts_fts USING fts5(
  post_id UNINDEXED, caption, author_username
);
-- triggers populate fts; author_username denormalized
CREATE TRIGGER posts_ai AFTER INSERT ON posts BEGIN
  INSERT INTO posts_fts(post_id, caption, author_username)
  VALUES (new.id, COALESCE(new.caption,''), new.author_username_denorm);
END;
CREATE TRIGGER posts_ad AFTER DELETE ON posts BEGIN
  DELETE FROM posts_fts WHERE post_id = old.id;
END;
CREATE TRIGGER posts_au AFTER UPDATE OF caption, author_username_denorm ON posts BEGIN
  DELETE FROM posts_fts WHERE post_id = old.id;
  INSERT INTO posts_fts(post_id, caption, author_username)
  VALUES (new.id, COALESCE(new.caption,''), new.author_username_denorm);
END;

CREATE TABLE media_files (
  sha256          TEXT PRIMARY KEY,
  file_path       TEXT NOT NULL,
  mime_type       TEXT,
  file_size_bytes INTEGER NOT NULL,
  width           INTEGER, height INTEGER,
  duration_seconds REAL,
  fetched_at      TEXT NOT NULL
);

CREATE TABLE post_media (
  id               INTEGER PRIMARY KEY,
  post_id          TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  media_sha256     TEXT NOT NULL REFERENCES media_files(sha256),
  thumbnail_sha256 TEXT REFERENCES media_files(sha256),
  carousel_index   INTEGER NOT NULL DEFAULT 0,
  media_type       TEXT NOT NULL,
  UNIQUE(post_id, carousel_index)
);
CREATE INDEX idx_post_media_post ON post_media(post_id);

CREATE TABLE collections (
  id            TEXT PRIMARY KEY,            -- IG numeric pk; 'all_posts' reserved
  name          TEXT NOT NULL,               -- mutable label; NO unique constraint
  is_all_posts  INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE post_collections (
  post_id       TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  added_at      TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,                -- updated every full enumeration
  PRIMARY KEY (post_id, collection_id)
);
CREATE INDEX idx_pc_collection ON post_collections(collection_id);

CREATE TABLE sync_runs (
  id                INTEGER PRIMARY KEY,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  state             TEXT NOT NULL,            -- 'running'|'ok'|'error'|'auth_required'
  triggered_by      TEXT NOT NULL CHECK (triggered_by IN ('schedule','manual','resume')),
  fully_enumerated  INTEGER NOT NULL DEFAULT 0,  -- BOOLEAN; gates unsaved-flagging
  posts_seen        INTEGER DEFAULT 0,
  posts_new         INTEGER DEFAULT 0,
  posts_unsaved     INTEGER DEFAULT 0,
  errors_json       TEXT
);
CREATE INDEX idx_sync_runs_started ON sync_runs(started_at DESC);

INSERT INTO schema_migrations(version, applied_at) VALUES (1, datetime('now'));
```

**Migrations contract:** every file is `NNN_name.sql`; boot applies any whose `version` is missing from `schema_migrations`, in order, each in its own transaction, then inserts the row. `001_init.sql` contains everything above. Schema is never edited in place; all changes ship as new numbered files.

**`post_collections` removal policy:** rows whose `last_seen_at < run.started_at` after a `fully_enumerated=true` run are deleted at end of run (post moved out of that collection on IG). Never deleted on partial runs.

---

## 3. Backend module responsibilities

- **`config.py`** — pydantic-settings; owns `HOST`, `PORT`, `DATA_DIR`, `ALLOW_REMOTE`, jitter/cadence, log level. Refuses non-loopback bind unless `ALLOW_REMOTE=1`.
- **`db/connection.py`** — SQLite with `check_same_thread=False`; **connection-per-thread** via `threading.local()`; one shared `RLock` serializes writes. Applies pragmas (WAL, NORMAL, foreign_keys) on each new connection. After each sync run, ingest worker calls `PRAGMA wal_checkpoint(TRUNCATE)` once to bound WAL growth. (Locks the trade-off in section 11: in-process ingest is fine because every thread that touches SQLite gets its own connection and writes are lock-serialized.)
- **`api/`** — thin HTTP; no SQL inline; dependency-injects per-request connection.
- **`db/repo.py`** — only place SQL strings live. `posts.upsert` uses `ON CONFLICT(id) DO UPDATE SET last_seen_in_saved_at=excluded.last_seen_in_saved_at, caption=excluded.caption, author_username_denorm=excluded.author_username_denorm` and **never touches `first_seen_at`**. Same write-once rule for `authors.first_seen_at` and `collections.first_seen_at`.
- **`ingest/`** — orchestration only. Owns reconcile algorithm (section 4), per-post BEGIN IMMEDIATE transactions, and `fully_enumerated` accounting. After successful run: `wal_checkpoint(TRUNCATE)`.
- **`ig_client/session.py`** — exposes `with_session_retry(callable)` decorator: on `LoginRequired`/`PleaseWaitFewMinutes` once, calls `relogin()` from persisted settings; on second failure, re-raises. Every instagrapi call category (collections list, items by collection, media_info) goes through it independently — one healthy call doesn't imply session-wide health.
- **`media/store.py`** — sha256 path layout; **streamed download** to `data/media/.tmp/<uuid>`, verifies bytes-written equals `Content-Length` (reject mismatch as `ShortReadError`), `os.fsync` then atomic `os.rename` into `<aa>/<sha>.<ext>`. Hash and DB insert happen only after rename succeeds. Partial files never live under a sharded path.
- **`scheduler/loop.py`** — single asyncio task: read auth state. **If state == `NEEDS_FIRST_LOGIN`, sleep one cadence interval and re-check; do NOT invoke `run_once` and do NOT write `auth_required` rows.** Otherwise `await asyncio.to_thread(run_once, …)`. Persists pause flag at `data/scheduler.state`.

---

## 4. Sync ingestion algorithm

```python
def run_once(db, ig, media_store, now, triggered_by):
    run_id = db.sync_runs.insert(started_at=now, state='running',
                                 triggered_by=triggered_by, fully_enumerated=0)
    fully_enumerated = False
    seen_post_ids = set()
    try:
        named = with_session_retry(ig.list_collections)()
        items_by_collection = {("all_posts", "All Posts"):
                               with_session_retry(ig.list_collection_items)("All Posts")}
        for col in named:
            items_by_collection[(col.id, col.name)] = \
                with_session_retry(ig.list_collection_items)(col.name)
        # If we got here, every collection enumerated successfully
        fully_enumerated = True

        for (cid, cname), items in items_by_collection.items():
            db.collections.upsert(cid, cname, last_seen_at=now,
                                  is_all_posts=(cid == "all_posts"))
            for it in items:
                seen_post_ids.add(it.id)
                with db.tx_immediate():                       # BEGIN IMMEDIATE … COMMIT
                    db.authors.upsert(it.user, last_seen_at=now)
                    db.posts.upsert(it, last_seen_in_saved_at=now, first_seen_at=now)
                    db.posts_raw.upsert(it.id, json.dumps(it.dict()))
                    db.post_collections.upsert(it.id, cid,
                                               added_at=now, last_seen_at=now)
                    for slide_idx, res in enumerate(it.resources_or_self()):
                        sha   = media_store.fetch_and_store(res.url)            # see below
                        thumb = media_store.fetch_and_store(res.thumbnail_url) \
                                if res.thumbnail_url else None
                        db.post_media.upsert(it.id, slide_idx, sha, thumb,
                                             media_type=res.kind)

        # Reconcile: clear flags for everything seen this run
        for pid in seen_post_ids:
            db.posts.set_flags(pid, is_unsaved=0, is_source_deleted=0)

        # Only flag missing as unsaved when we know the set was complete
        if fully_enumerated:
            prior = db.posts.ids_where(is_unsaved=0)
            for missing in (prior - seen_post_ids):
                db.posts.set_flags(missing, is_unsaved=1)
            # Sweep stale collection memberships
            db.post_collections.delete_where_last_seen_before(run_started_at=now)

        db.sync_runs.update(run_id, state='ok', finished_at=clock.now(),
                            fully_enumerated=int(fully_enumerated),
                            posts_seen=len(seen_post_ids),
                            posts_new=db.sync_runs.count_new(run_id))
    except ChallengeRequired:
        db.sync_runs.update(run_id, state='auth_required', finished_at=clock.now(),
                            fully_enumerated=int(fully_enumerated))
    except Exception as e:
        db.sync_runs.update(run_id, state='error', finished_at=clock.now(),
                            fully_enumerated=int(fully_enumerated),
                            errors_json=json.dumps({"err": str(e)}))
    finally:
        db.checkpoint_wal_truncate()                          # bounds WAL growth
```

**`media_store.fetch_and_store(url)` contract:**

```
1. open httpx stream → tmp = data/media/.tmp/<uuid>
2. write chunks to tmp; track bytes_written
3. if Content-Length present and bytes_written != Content-Length → unlink tmp; raise ShortReadError
4. fsync(tmp); compute sha256 of file on disk (not in-memory bytes)
5. final = data/media/<sha[:2]>/<sha>.<ext>; os.rename(tmp, final)
6. if media_files row absent: insert (sha, file_path, size, mime, dimensions, fetched_at)
7. return sha
```

Liveness sweep is **dropped from v1** (CDN URLs rotate; HEAD false-positives constantly). `is_source_deleted` is only set when an explicit `ig.media_info(pid)` lookup raises `MediaNotFound` — invoked lazily from API path on a single post when UI requests it (B5 stretch), not in scheduled runs.

---

## 5. Re-auth / 2FA UX

```
states: NEEDS_FIRST_LOGIN → CHALLENGE_PENDING → LOGGED_IN → SESSION_EXPIRED ↺
```

- **NEEDS_FIRST_LOGIN** — no `instagrapi_settings.json` on disk. Scheduler is **dormant** (sleeps cadence, re-checks; no `sync_runs` rows written, no error spam). Operator runs `python -m backend.ig_client login` once.
- **LOGGED_IN** — scheduler ticks normally; each instagrapi call wrapped via `with_session_retry()`.
- **SESSION_EXPIRED** — first call raises `LoginRequired`; wrapper retries via `relogin()`; success → LOGGED_IN; second `LoginRequired` → bail, mark run `state='auth_required'`.
- **CHALLENGE_PENDING** — `ChallengeRequired` mid-run aborts the run. `fully_enumerated` stays 0 → unsaved-flagging is **skipped**, partial post upserts stand (no false unsaves of collections 5–9). Scheduler stops ticking until UI resolves it.

Endpoints:

```
POST /api/auth/challenge {code}    → resolve + dump_settings(); state→LOGGED_IN
GET  /api/auth/status              → {state, challenge_kind, last_error, since}
```

Frontend banner is yellow on `auth_required` / `CHALLENGE_PENDING` with an input; full-page `/auth` route on `NEEDS_FIRST_LOGIN`.

---

## 6. API endpoints

All under `/api`, JSON, server bound to `127.0.0.1`.

```ts
interface Author { id: string; username: string; full_name: string|null;
                   profile_pic_url: string|null; is_private: boolean; }
interface MediaSlide { sha256: string; thumbnail_sha256: string|null;
                      media_type: 'image'|'video'; width: number; height: number;
                      duration_seconds: number|null; carousel_index: number; }
interface Post {
  id: string; shortcode: string; caption: string|null;
  media_kind: 'image'|'video'|'carousel';
  taken_at: string|null; saved_at: string|null;
  first_seen_at: string; last_seen_in_saved_at: string;
  is_unsaved: boolean; is_source_deleted: boolean;
  author: Author; slides: MediaSlide[]; collections: Collection[];
}
interface Collection { id: string; name: string; is_all_posts: boolean;
                       post_count: number; }
interface SyncStatus { state: 'idle'|'running'|'ok'|'error'|'auth_required'|'paused';
                       last_run: { started_at: string; finished_at: string|null;
                                   triggered_by: 'schedule'|'manual'|'resume';
                                   fully_enumerated: boolean;
                                   posts_seen: number; posts_new: number;
                                   errors: object|null } | null;
                       next_run_at: string|null; paused: boolean; }
interface AuthStatus { state: 'NEEDS_FIRST_LOGIN'|'CHALLENGE_PENDING'|
                              'LOGGED_IN'|'SESSION_EXPIRED';
                       challenge_kind: 'sms'|'email'|'totp'|null;
                       last_error: string|null; }
```

**Cursor format (locked):** `cursor = base64(json([COALESCE(saved_at, first_seen_at), id]))`. The list query is:

```sql
SELECT … FROM posts
WHERE (... filters ...)
  AND (:cursor_ts IS NULL OR
       (COALESCE(saved_at, first_seen_at), id) < (:cursor_ts, :cursor_id))
ORDER BY COALESCE(saved_at, first_seen_at) DESC, id DESC
LIMIT :limit
```

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/posts?collection_id=&q=&since=&until=&include_unsaved=&cursor=&limit=` | returns `{items, next_cursor}` |
| GET  | `/api/posts/:id` | full Post |
| GET  | `/api/collections` | sorted: `is_all_posts` first |
| GET  | `/api/sync/status` | `SyncStatus` |
| GET  | `/api/sync/runs?limit=20` | recent run history (debug pane) |
| POST | `/api/sync/run-now` | **202** if accepted (queues `triggered_by='manual'`); **409** if a run is already in progress |
| POST | `/api/sync/pause` / `/api/sync/resume` | toggles persisted flag |
| GET  | `/api/media/:sha256` | see below |
| GET  | `/api/auth/status`, `POST /api/auth/challenge` | section 5 |

**`GET /api/media/:sha256` (locked behavior):**

```
ETag: "<sha256>"
Cache-Control: public, max-age=31536000, immutable
Accept-Ranges: bytes

If request header If-None-Match == "<sha256>": respond 304 (no body).
If request header Range: bytes=START-END (or open-ended):
  Validate; respond 206 with Content-Range: bytes START-END/TOTAL,
  Content-Length set to slice size, body is the requested byte range.
  Invalid range → 416 with Content-Range: bytes */TOTAL.
Else: respond 200 with full body and Content-Length.
```

Range support is mandatory (Safari `<video>` seeking depends on it for B2). Implementation streams from `open(file_path, 'rb').seek()` with `iter_chunks`.

---

## 7. Frontend structure

```
frontend/src/
├── main.tsx
├── App.tsx                 # router + QueryClientProvider + AuthBanner
├── routes/{AllPosts,Collection,PostModal,Sync,Auth}.tsx
├── components/{PostGrid,PostThumb,PostLightbox,FilterBar,
│              CollectionList,SyncStatusCard,AuthBanner,ui/*}.tsx
├── lib/{api,queryKeys,mediaUrl}.ts
└── styles/index.css
```

Routes via react-router. PostModal is a parallel/overlay route. Filter state lives in URL params. TanStack Query keys:

```
['posts', { collection_id, q, since, until, include_unsaved }]   // infinite
['post', id]
['collections']
['sync','status']                                                // poll 10s
['sync','runs']                                                  // on Sync page
['auth','status']                                                // poll 10s
```

`mediaUrl(sha)` returns `/api/media/${sha}`; `<img>` and `<video>` get free 304/Range from section 6.

---

## 8. Test strategy

- **Unit** — `ingest/reconcile.py` (DB-state × IG-set → flag deltas, including re-save clearing); `media/store.py` (sha path + atomic-write + ShortReadError on truncated `Content-Length`); migrations applied to `:memory:` and `schema_migrations` populated; `db/repo.py` cursor predicate builder.
- **Integration** — vcrpy/fake-IG replays `collections() / collection_medias_by_name()` into `run_once()` against tmp SQLite + tmp media root; assert (a) re-saved post clears `is_unsaved=0`, (b) partial run with `fully_enumerated=0` does NOT mark missing posts unsaved, (c) `/api/media/:sha256` returns 304 on `If-None-Match` and 206 on `Range`.
- **Contract** — FastAPI TestClient assertions match the TS interfaces in section 6.
- **Out of CI** — no live IG calls.

---

## 9. Vertical slice scope (Phase 3)

Smallest end-to-end demo. Single CLI + minimal grid.

1. `python -m backend.ig_client login` → writes `data/instagrapi_settings.json`. **Required first.**
2. `python -m backend.ingest.runner --once --first-page-only` → triggered_by=`manual`, fetches first page of "All Posts," upserts authors/posts, downloads media via `media_store.fetch_and_store` (atomic + Content-Length verify). No collections, no liveness, no scheduler.
3. `uvicorn backend.main:app` (binds `127.0.0.1` by default; refuses other binds without `ALLOW_REMOTE=1`). Exposes only `GET /api/posts` (no filters) and `GET /api/media/:sha256` (full Range + 304 handling). Scheduler started by lifespan but **dormant** while `NEEDS_FIRST_LOGIN`; once login ran in step 1, it ticks normally — but B4 ships the cadence/jitter, so for slice purposes we just demonstrate it stays idle.
4. Vite frontend with one route `/` rendering 3-column grid using `PostGrid` + `PostThumb`.

Demo: log in, run sync once, open `localhost:5173`, see real saved posts.

---

## 10. Iterative build batches (Phase 4)

- **B1 — Collections + M:M.** `collections()` + `collection_medias_by_name()` wired into `run_once`; `post_collections.last_seen_at` policy (section 2); `GET /api/collections`; `CollectionList` + `/collections/:id` route. Verify: post in 3 collections has 3 join rows; removing a post from a collection on IG deletes the row after one full run.
- **B2 — Post detail modal.** YARL with Video/Captions/Counter; `/posts/:id` overlay; full Post payload. Verify on Safari: video seeking works (Range), thumb 304s on revisit (If-None-Match).
- **B3 — Search + filters.** FTS5 triggers (already in section 2); `q=`, `since`/`until`, `include_unsaved`; `FilterBar` writes URL params. Verify: caption hits, denormalized author_username searchable.
- **B4 — Scheduler + sync UI.** asyncio loop with `24h ± uniform(-1h,+1h)`; `data/scheduler.state`; `/api/sync/status|run-now|pause|resume|runs`; `Sync.tsx` with last-run card + history. Verify: 409 on double-fire; pause survives restart; `wal_checkpoint(TRUNCATE)` runs each cycle.
- **B5 — 2FA / re-auth.** State machine, `/api/auth/*`, `AuthBanner`, challenge input. Verify: forced expiry → banner → code → resumed run.
- **B6 — macOS deployment.** `ops/com.genehan.iglogger.plist` with ThrottleInterval/StandardOut/StandardErr/EnvironmentVariables; `just install-launchd`/`uninstall-launchd`; `just backup` + crontab snippet excluding `data/backups/` from `tmutil`. Verify: reboot → service auto-starts → next sync runs.

---

## 11. Locked trade-offs (no longer open)

1. **asyncio loop** (not APScheduler) — one job, no cron, APScheduler's job-store fights SQLite WAL.
2. **launchd** (not docker) — Docker on macOS adds Linux VM and network hop.
3. **`/api/media/:sha256`** (not StaticFiles) — earns its keep with `If-None-Match→304` + `Range`/`Accept-Ranges: bytes` (section 6, mandatory).
4. **In-process ingest** — fine because section 3 mandates connection-per-thread + write lock; `asyncio.to_thread` isolates the long-running work.
5. **No UI auth, but bind 127.0.0.1 explicitly** — `config.py` refuses other binds without `ALLOW_REMOTE=1`.
6. **WAL + `synchronous=NORMAL`** — with `PRAGMA wal_checkpoint(TRUNCATE)` after each sync run (section 3, ingest `finally`).

---

## 12. Critic findings → resolution map

| # | One-line resolution | Plan section |
|---|---|---|
| Must-1 | Reconcile clears `is_unsaved=0` for every pid in `seen_post_ids` (and `is_source_deleted=0`) | §4 |
| Must-2 | Repo `posts/authors/collections.upsert` uses `ON CONFLICT DO UPDATE` that never touches `first_seen_at` | §3 (`db/repo.py`) |
| Must-3 | CDN-HEAD liveness sweep dropped from v1; `is_source_deleted` only via explicit `media_info(pid)` on demand | §4 |
| Must-4 | `post_collections.last_seen_at NOT NULL`; stale rows deleted only after `fully_enumerated=true` runs | §2, §4 |
| Must-5 | Collections keyed by IG numeric id only; `name` is mutable; UNIQUE on name dropped | §2 |
| Must-6 | Streamed download → tmp → verify Content-Length → fsync → atomic rename → hash-and-insert; ShortReadError on mismatch | §3 (`media/store`), §4 |
| Must-7 | Per-post `BEGIN IMMEDIATE … COMMIT` wrapping authors → posts → posts_raw → post_collections → post_media → media_files | §4 |
| Must-8 | Connection-per-thread (`threading.local()` + RLock; `check_same_thread=False`); pragmas applied per connection | §3 (`db/connection.py`) |
| Must-9 | `sync_runs.fully_enumerated BOOLEAN`; unsaved-flagging gated on it | §2, §4 |
| Must-10 | `with_session_retry()` wraps every instagrapi call category; one `relogin()` retry then bail | §3, §4, §5 |
| Must-11 | Cursor = `base64(json([COALESCE(saved_at, first_seen_at), id]))`; matching `ORDER BY` + tuple `WHERE` predicate | §6 |
| Must-12 | `/api/media/:sha256` implements `If-None-Match→304`, `Accept-Ranges: bytes`, and `Range` (206/416) | §6 |
| Must-13 | Scheduler dormant in `NEEDS_FIRST_LOGIN`; no run rows written | §3, §5 |
| Must-14 | `schema_migrations(version PK, applied_at)` tracking table; `001_init.sql` is canonical first migration | §1, §2 |
| Should-1 | FTS5 AFTER INSERT/UPDATE/DELETE triggers populate `posts_fts(post_id, caption, author_username)` with denormalized author | §2 |
| Should-2 | `posts.raw_json` moved into sibling `posts_raw(post_id PK, json)` table | §2, §4 |
| Should-3 | `authors.profile_pic_sha256` FK dropped; replaced with nullable `profile_pic_url` (no media download in v1) | §2 |
| Should-4 | `sync_runs.triggered_by` (CHECK in 'schedule'/'manual'/'resume') and `fully_enumerated` columns added | §2, §6 |
| Should-5 | launchd plist: `ThrottleInterval=60`, `StandardOutPath`, `StandardErrorPath`, `EnvironmentVariables` for `DATA_DIR`/`HOST`/`PORT`/`ALLOW_REMOTE` | §1 |
| Should-6 | `just backup` runs `sqlite3 ".backup"`; `data/backups/` excluded from Time Machine | §1 |
| Should-7 | `POST /api/sync/run-now` returns **202** accepted, **409** if already running | §6 |
| Nice-1 | `posts.author_username_denorm` column kept in sync via FTS-feeding triggers | §2 |
| Nice-2 | `data/media/.tmp/` reserved for in-flight downloads; final files only under sharded paths | §1, §3, §4 |
| Nice-3 | `GET /api/sync/runs?limit=20` for debug history pane | §6, §10 (B4) |
