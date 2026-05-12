# Deep Interview Spec: instagram-logger extension pivot

## Metadata
- Interview ID: di-2026-05-12-extpivot
- Rounds: 4
- Final Ambiguity Score: ~9%
- Type: brownfield
- Generated: 2026-05-12
- Threshold: 20%
- Initial Context Summarized: no
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.95 | 0.35 | 0.333 |
| Constraint Clarity | 0.90 | 0.25 | 0.225 |
| Success Criteria | 0.92 | 0.25 | 0.230 |
| Context Clarity | 0.85 | 0.15 | 0.128 |
| **Total Clarity** | | | **0.915** |
| **Ambiguity** | | | **0.085** |

## Goal

Ship a Chrome MV3 extension that runs on the user's always-on Mac mini in their personal Chrome profile, scrapes the saved-posts list (and per-collection mappings) of the user's primary Instagram account using stealth-first, human-like pacing, and ships post payloads + media bytes to the existing FastAPI backend for storage in SQLite + a sharded media filesystem, browseable via the existing React frontend.

**Division of responsibility:**
- **Extension** owns: enumeration, post-detail extraction, authenticated media fetching, jitter/scheduling.
- **Backend** owns: storage, retry orchestration metadata, the read API, the new ingest API.
- **User** owns: manual login (no automation), and manual retry of failed posts via UI buttons.

The pivot abandons the prior Python `instagrapi` / `instaloader` approach entirely because the Mac mini's device fingerprint is flagged at IG's CDN level (see `.omc/SESSION_HANDOFF.md` for the postmortem). The extension acts within the user's real Chrome session — indistinguishable from normal browsing.

## Constraints

- Manual login only. No automation of password / 2FA / challenges. Extension never touches login forms.
- No backend → IG traffic. Bytes enter the system exclusively via the extension's authenticated `fetch()` in the IG tab context.
- One media fetch at a time, anywhere in the extension. Memory bounded to a single Blob (≤ ~50MB worst case).
- Awake window: 08:00–01:00 local Mac mini time. Outside that window: zero network/DOM activity.
- One randomly-selected rest day per ISO week. Zero activity that day.
- Bursts: 180–900s active, 30–180min inter-burst gap. Intra-burst scroll jitter 800–4000ms; post-detail dwell 1500–8000ms; media spacing 400–1800ms.
- Discovery scroll: top → bottom of `/saved/all-posts/` (forced by IG; older posts load by scrolling down). Capture `recency_rank` (0=newest, N=oldest).
- Enrichment order: **oldest first** via `ORDER BY recency_rank DESC`.
- No coexistence logic with human IG browsing in other tabs. Extension runs on its jitter schedule regardless. Signal-correlation risk accepted in exchange for simplicity.
- No reconcile semantics. No `is_unsaved` flag. No `is_source_deleted` flag. No full re-enumeration after initial discovery. Archive is append-only after discovery.
- Watch mode: top-peek of `/saved/all-posts/` every 12–24h (jittered). Only purpose: catch newly-saved posts.
- Backend bound to `127.0.0.1` (existing rule, unchanged). All ingest endpoints gated by `X-Ingest-Secret` header.

## Non-Goals

- ❌ `instagrapi`, `instaloader`, or any HTTP-level IG API call from backend.
- ❌ Headless Chrome / Puppeteer / Playwright.
- ❌ Automated login (password fill, 2FA, challenge resolution).
- ❌ Reflecting IG state changes (unsaves, deletes, collection removals) in the archive after a post is captured.
- ❌ Full re-enumeration of `/saved/` after initial discovery.
- ❌ Automatic retry beyond the initial 3 attempts. Manual retry button is the escape valve.
- ❌ Reflecting collection-membership changes after initial discovery (v1 scope; can revisit later).
- ❌ Cross-account support. Single primary Instagram account only.
- ❌ Multi-user support. Single-user, self-hosted on Mac mini, localhost-only.

## Acceptance Criteria

- [ ] Extension installs unpacked, accepts shared `INGEST_SECRET` via popup, successfully round-trips `GET /api/ingest/extension/state`.
- [ ] Auth-watch content script detects "logged out" state (login wall on `/saved/`) and triggers a heartbeat with `state='logged_out'`. Backend calls `notify.telegram.alert()` (stub: writes to log file).
- [ ] Discovery phase scrolls `/saved/all-posts/` top → bottom, capturing every shortcode + a stable `recency_rank` (newest=0). End-of-list detected by scroll height stable across 5 consecutive attempts.
- [ ] Each named collection separately enumerated; `post_collections` join rows populated.
- [ ] Enrichment phase pulls oldest-first (`ORDER BY recency_rank DESC`) and processes each post in jittered humanlike sequence.
- [ ] Post-state machine: `placeholder → enriched | lost`. 3 auto-tries with backoff (30min / 2h / 12h). Hard 404 → `lost` immediately + one 7-day sanity re-check. After that, no more auto-retries.
- [ ] Media-state machine (per slide): `pending → present | media_failed`. 3 tries with same URL, then re-visit post for fresh URLs, 2 more tries with fresh URLs, then `media_failed`.
- [ ] Media bytes uploaded via `POST /api/ingest/extension/media` (multipart). Backend re-hashes and rejects on sha mismatch. Atomic write via existing `media/store.py`.
- [ ] Pre-upload dedup: `HEAD /api/ingest/extension/media/exists?sha=<>` returns 204 for known sha, 404 for unknown.
- [ ] Watch-mode gate: extension flips from `enrichment` to `watch` when `(enriched + lost) == total_discovered`.
- [ ] Watch mode performs a top-peek every 12–24h (jittered). New shortcodes enter at front of enrichment queue with elevated priority.
- [ ] `POST /api/posts/:id/retry-page` resets post to `placeholder` state, sets `next_retry_at=now`, sends a one-shot trigger to extension.
- [ ] `POST /api/posts/:id/retry-media/:slide_idx` sets that slide to `pending`, resets retry_count, queues a re-visit.
- [ ] Frontend tile states:
  - `enriched` + all media `present` → normal post tile.
  - `enriched` + some `media_failed` → post tile with broken-image placeholder for failed slides.
  - `lost` → greyed-out tombstone tile with author username + last-seen timestamp + "Retry" button.
  - `placeholder` (in retry window) → skeleton tile.
- [ ] Single "Retry" button on each card hits the contextually-correct endpoint (no UI distinction).
- [ ] No backend code makes outbound HTTP requests to `instagram.com` / `*.cdninstagram.com` / `*.fbcdn.net`. Verified by static grep + dependency analysis.
- [ ] After E0 cleanup: no references in repo to `instagrapi`, `instaloader`, `scheduler`, `reconcile`, `ig_client`, `ingest.runner`. Verified by grep.
- [ ] All existing repo + media + posts/collections API tests pass.
- [ ] New endpoint tests pass: state, collections, shortcodes, membership, post, media (exists + upload), heartbeat, retry-page, retry-media.
- [ ] Fake-IG fixture site (local static HTML at `tests/fixtures/fake-ig/`) supports end-to-end extension smoke: discovery → collections → enrichment with one failed media → tombstone with retry → media success.
- [ ] No automation warning appears in normal Chrome browsing on `instagram.com` after one full week of extension activity (manual verification at E7).

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| Plan needed reconcile / unsave tracking from original `plan.md` §11 | Archive is keep-forever; why mirror IG state? | Drop both. Append-only after discovery. |
| Enrichment direction is irrelevant | Old posts have higher link-rot risk (deletes, account-gone-private, CDN signature expiry) | Oldest-first via `recency_rank DESC`. Stealth bonus: "caught up + watching top" mimics a real user. |
| Extension should detect human IG browsing and yield | Cost-vs-benefit of overlap detection | No coexistence logic. Extension runs on jitter regardless. Simplicity > theoretical correlation safety. |
| "permanent_fail" was a single state | Many failure modes; hard vs transient vs partial | Two state machines (post + media), with explicit failure types and manual retry. |
| Watch mode should periodically re-enumerate | Why? Only need to catch new saves. | Top-peek only, every 12–24h. No full re-enum, ever. |
| Auto-retries should continue forever for transient failures | Costs heat; manual button is sufficient | Stop after 3 auto-tries. UI button is the escape valve. |
| Backend should fetch media from CDN as fallback | Re-introduces device-IP fingerprint risk | No. Backend never touches IG CDN. Extension is the sole fetcher; failures become `media_failed` tombstones. |

## Technical Context

**Existing repo:** `/Users/genehan/projects/claudehome-projects/gene-mini_suite/instagram-logger`

**Backend stack:** FastAPI + SQLite WAL, connection-per-thread + RLock, atomic media writes with sha256, 127.0.0.1 binding, Range/304 on `GET /api/media/:sha256`.

**Frontend stack:** Vite + React + TypeScript + TanStack Query + react-router + yet-another-react-lightbox (for the existing PostModal).

**Existing schema** (kept as-is, plus migrations 002 / 003):
- `authors`, `posts`, `posts_raw`, `posts_fts` (FTS5), `media_files`, `post_media`, `collections`, `post_collections`, `sync_runs`, `schema_migrations`.

**New deps:**
- Extension toolchain: `@crxjs/vite-plugin` (or equivalent), `vite`, `typescript`, `chrome-types`.
- No new backend deps. `httpx` stays only for testing fixtures, not IG calls.

**Removed deps:** `instagrapi`, `instaloader` (and its `[web-fallback]` extra).

**Locked architectural choices that carry over from original `plan.md` §11:**
- asyncio (where used internally) over APScheduler.
- launchd over docker.
- `/api/media/:sha256` endpoint over StaticFiles (Range / 304 mandatory).
- Connection-per-thread + write-RLock for SQLite.
- 127.0.0.1 bind with `ALLOW_REMOTE=1` escape hatch.
- WAL + `synchronous=NORMAL` + post-run `wal_checkpoint(TRUNCATE)` (the post-run trigger moves into the ingest endpoints rather than a scheduler).

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| Extension | core | phase, jitter scheduler, storage, secret | drives Chrome Tab; POSTs to Backend |
| Backend | core | endpoints, DB, media store, secret | receives from Extension; serves Frontend |
| Chrome Tab | core | URL, active state, content script | hosted by Extension's controller |
| Phase | core | name ∈ {idle, discovery_all, discovery_collections, enrichment, watch, paused, logged_out}, transitions, gate predicate | drives Extension behavior |
| Burst | supporting | duration, gap, jitter ranges, awake-window guard | scheduled by Extension |
| Post | core domain | shortcode, recency_rank, state ∈ {placeholder, enriched, lost}, retry_count, next_retry_at, caption, author_id, payload_fetched_at | belongs to many Collections; has many MediaFiles via post_media; tombstone-renderable |
| Collection | core domain | id, name, is_all_posts | contains many Posts |
| MediaFile | core domain | sha256, file_path, mime, size | referenced by Post via post_media (per-slide state) |
| TelegramAlert | supporting / external | severity, message, rate-limit window | sent by Backend on logged_out heartbeat; stub for v1 |
| SessionCookie | external | in user's Chrome profile (managed by user manually) | used implicitly by Extension's `fetch(url, {credentials:'include'})` |
| RetryAction | UI | target endpoint, target post_id, optional slide_idx | user-initiated against Post (page) or MediaFile (slide) |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 10 | 10 | - | - | N/A |
| 2 | 10 | 0 | 0 | 10 | 100% |
| 3 | 10 | 0 | 0 | 10 | 100% |
| 4 | 11 | 1 (RetryAction) | 0 | 10 | 91% |

The domain model was stable across the entire interview. RetryAction is a UI-side concept introduced in round 4 by the manual-retry-button decision; it doesn't shift the core domain.

## Interview Transcript

<details>
<summary>Full Q&A (4 rounds)</summary>

### Round 1
**Targeting:** Success Criteria (0.30) — plan never defined "done."
**Q:** What does "done with the initial 1–2 week crawl" mean for you — when should the extension flip from `enrichment` to `watch`?
**A:** Tolerant best-effort. Per-post 3-try budget; flip to watch when `(enriched + permanent_fail) == total`. Accept ~1–5% loss.
**Ambiguity:** 36% → 26% (Goal: 0.85, Constraints: 0.55, Criteria: 0.70 ↑, Context: 0.85)

### Round 2
**Targeting:** Constraints (0.55) — multi-tab coexistence undefined.
**Q:** When you're actively browsing instagram.com yourself in another tab (same Chrome profile), what should the extension do?
**A:** Don't coordinate. No coexistence logic. Extension runs on jitter regardless of human IG activity. Simplicity > correlation-risk avoidance.
**Ambiguity:** 26% → 24% (Goal: 0.85, Constraints: 0.65 ↑, Criteria: 0.70, Context: 0.85)

### Round 3
**Targeting:** Constraints (0.65) — re-enumeration cadence undefined.
**Q:** How often should the extension re-enumerate saved tab during watch mode?
**A (clarifying first):** Why re-iterate at all?
**Resolution after dialogue:**
- Archive is keep-forever; we don't need to mirror IG state changes.
- Drop unsave tracking, drop full re-enumeration. Drop `is_unsaved` / `is_source_deleted` flags.
- Watch mode = top-peek of `/saved/all-posts/` every 12–24h, only to catch new saves.
- Also resolved: enrichment proceeds **oldest first** (after full discovery scroll), to mitigate link-rot risk and to make watch-mode behavior mimic a user who is "caught up."
**Ambiguity:** 24% → 12% (Goal: 0.95 ↑, Constraints: 0.85 ↑, Criteria: 0.85 ↑, Context: 0.85)

### Round 4
**Targeting:** Constraints (0.85) — "permanent_fail" was a single state, but real failure modes differ.
**Q:** How are we handling failed posts?
**Proposal:** Two state machines:
- Post-level: `placeholder → enriched | lost`. 3 auto-tries (30min / 2h / 12h). Hard 404 → `lost` immediately.
- Media-level (per slide): `pending → present | media_failed`. 3 tries on URL, then re-visit for fresh URL, 2 more tries.
**A:** Tombstones with manual retry button.
**Implication resolved:** No forever-auto-retry. After 3 auto-tries, posts/slides stop trying. Manual UI button is the escape valve.
**Ambiguity:** 12% → 9% ✅ (Goal: 0.95, Constraints: 0.90 ↑, Criteria: 0.92 ↑, Context: 0.85)

</details>

## Notes for Execution

1. **Pivot artifact:** Implementation plan lives at `.omc/plans/extension-pivot.md` (updated in lock-step with this spec).
2. **Real-IG verification gated on Resume A cooloff:** Per `.omc/SESSION_HANDOFF.md`, the dev MBP's device flag is at day 5 of a 7–14 day cooloff window. Smoke verification (extension batch E7) waits until day 14 or until a different machine is available.
3. **Telegram is a stub:** `backend/notify/telegram.py` initially just logs. Real wiring is gated on E8 after E7 verification confirms the extension is safe to run.
4. **Pre-execution recommendation:** route through ralplan (omc-plan --consensus --direct) for one consensus pass before invoking autopilot, so Planner/Architect/Critic can challenge the schema changes and the MV3 lifecycle assumptions before any code is written.
