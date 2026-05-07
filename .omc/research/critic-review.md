# Critic review of plan v1

**Verdict:** REVISE

## Must fix before Phase 3

1. Section 4 reconcile loses re-saved posts. `is_unsaved` flag is only ever flipped to 1; if user un-saves then re-saves, next run sees post in `seen_post_ids` but never clears the flag. Add: for every `pid in seen_post_ids`, set `is_unsaved=0`. Same for `is_source_deleted` if a 404 was transient.

2. Section 4 step 3 always overwrites `first_seen_at`. Repo `upsert` must use `ON CONFLICT … DO UPDATE SET last_seen_in_saved_at=excluded.last_seen_in_saved_at` and explicitly NOT touch `first_seen_at`. Same defect applies to `authors.upsert` and `collections.upsert`.

3. Section 4 step 6 liveness is wrong. CDN URLs rotate and 403/404 routinely without the post being deleted — HEAD on `first_media_url` will false-positive `is_source_deleted` constantly. Use `ig.media_info(pid)` (catches `MediaNotFound`) or drop the sweep entirely from v1 and rely on "missing from saved + can't refetch on demand."

4. Section 2: `post_collections` cannot represent removal from a single collection. No `last_seen_at` and no removal flag. If post stays saved but moves out of "Inspiration", row is never deleted. Add `last_seen_at TEXT NOT NULL` on `post_collections`; either delete stale rows post-run or add `removed_at`. Spec the policy.

5. Section 2: collection identity is unstable. Schema claims IG numeric pk but uses `__all__` sentinel and is keyed by name. Renaming a collection on IG breaks `idx_collections_name UNIQUE`. Decide: key by IG numeric id always, name is a mutable label. Drop UNIQUE on name.

6. Section 4: media download has no transactional boundary; sha256 collisions on truncation are real. If httpx returns partial bytes, you compute sha256 of truncated file and persist a corrupt `media_files` row that gets deduped against forever. Required: stream to temp file, verify `Content-Length` matches bytes written, fsync, rename, *then* hash-and-insert; reject on length mismatch. Document in section 3 under `media/`.

7. Section 4: SQLite write inside per-slide loop with no transaction. Every `db.*.upsert` is autocommitted, so mid-run crash leaves half-ingested post with some `post_media` rows missing. Wrap each post (authors → posts → post_collections → all post_media → media_files) in a single `BEGIN IMMEDIATE … COMMIT`.

8. Section 4 + scheduler: API and scheduler share one connection but `to_thread` runs sync SQLite calls. A single `sqlite3.Connection` is not thread-safe and will deadlock or raise `ProgrammingError` once scheduler thread and API request both touch it. Mandate: connection-per-thread (or per-request) with `check_same_thread=False` *only* if you also serialize via a lock, or use SQLAlchemy with a connection pool. Document the chosen pattern in section 3.

9. Section 5 state machine: 2FA mid-sync is undefined. If `ChallengeRequired` raised after collection 4 of 9, run aborts but partial collections are committed. Next run starts fresh with stale `last_seen_in_saved_at` for collections 5-9, which the unsaved-flagging step then incorrectly marks as unsaved. Required: only run "flag missing as unsaved" if run *fully enumerated all collections*. Gate it on `fully_enumerated` boolean; persist on `sync_runs`.

10. Section 5: "saved session works for /feed/saved/ but fails for /collections/list/" not addressed. Treat each instagrapi call category that can raise `LoginRequired`/`PleaseWaitFewMinutes` independently; do not assume one success means session is healthy for the whole run. Add per-call wrapper that on `LoginRequired` once attempts `relogin()` then bails if it raises again.

11. Section 6: cursor format is broken. `base64(saved_at | id)` — `saved_at` is nullable. Cursor must be `(COALESCE(saved_at, first_seen_at), id)` and `ORDER BY` and `WHERE (x,y) < (?,?)` predicate must use the same expression. Spec it explicitly.

12. Section 6: `/api/media/:sha256` ETag is misleading without `If-None-Match → 304` handling. Also: missing `Accept-Ranges: bytes` will break `<video>` seeking on Safari for any non-trivially-sized video — required for B2.

13. First-launch story missing from section 9. Vertical slice runs `uvicorn` which starts the scheduler; with no `instagrapi_settings.json`, scheduler tick will hit `NEEDS_FIRST_LOGIN` on every wake. Scheduler must skip ticking (not spam `auth_required` rows) when state is `NEEDS_FIRST_LOGIN`. Add to section 3's scheduler responsibility.

14. Migrations contract under-specified. Section 1 says "numbered .sql files, applied at boot" but section 2 ships schema as one `schema.sql`. Pick one: `schema.sql` is `001_init.sql` and there's a `schema_migrations(version)` table tracked, or this fails on second deploy. Add tracking table to section 2.

## Should fix before Phase 4

1. FTS5 trigger spec missing. `posts_fts` declared as contentless but no triggers populate it. B3 will return zero results. Spec `AFTER INSERT/UPDATE/DELETE` triggers including `author_username` denormalization.
2. `raw_json TEXT NOT NULL` will balloon DB. Carousel post dicts are 30-100KB. At 5k posts that's ~500MB in `posts.raw_json`. Either gzip, or move to a sibling `posts_raw(post_id, json)` table.
3. `profile_pic_sha256` FK references `media_files` but pic downloads aren't in section 4. Either add download step or drop FK to a nullable URL.
4. `sync_runs` has no `triggered_by` ('schedule'|'manual'|'resume') or `fully_enumerated` field. Needed for finding 9 and the run-now indicator.
5. B6 launchd plist needs `ThrottleInterval`, `StandardOutPath`/`StandardErrorPath`, and `EnvironmentVariables` for `DATA_DIR`. Otherwise crash-loop is unbounded and logs go to a default location nobody knows.
6. No backup story. Add `just backup` running `sqlite3 app.db ".backup data/backups/app-<ts>.db"` (WAL-safe) on a separate cron, and exclude `data/backups/` from `tmutil`.
7. `POST /api/sync/run-now` "ignored if already running" needs a contract. Return 409, not 202, when busy.

## Nice to have

1. Add `posts.author_username_denorm` column kept in sync via trigger to avoid joining for grid queries.
2. Spec `data/media/.tmp/` dir for in-flight downloads so partial files never live under a sharded path.
3. Add `GET /api/sync/runs?limit=20` endpoint for a debug history pane in `Sync.tsx`.

## Trade-off verdicts (locked)

- asyncio loop **(keep)** vs APScheduler — one job, no cron, APScheduler's job-store fights SQLite WAL.
- launchd **(keep)** vs docker — Docker on macOS adds Linux VM and network hop to every instagrapi call.
- `/api/media/:sha256` endpoint **(keep, conditional)** — only if you add `If-None-Match → 304` + `Range` support. Otherwise StaticFiles is actually better.
- In-process ingest **(keep)** — fine *if* finding 8 (connection-per-thread) is fixed.
- No UI auth on localhost **(keep)** — but bind explicitly to `127.0.0.1`, refuse `0.0.0.0` unless `ALLOW_REMOTE=1`.
- WAL + `synchronous=NORMAL` **(keep)** — correct, but add `PRAGMA wal_checkpoint(TRUNCATE)` after each sync run.
