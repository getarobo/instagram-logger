# Extension pivot — RALPLAN-DR consensus overlay

**Date:** 2026-05-12 (revised iteration 2 of ralplan loop, integrates Architect REVISE + Critic ITERATE feedback)
**Mode:** SHORT (default). Single-user, localhost-bound, git/DB rollback available, no production traffic.
**Inputs cited (not duplicated):**
- Spec: `.omc/specs/deep-interview-extension-pivot.md` (ambiguity 9%, PASSED) — contract, untouched.
- Plan: `.omc/plans/extension-pivot.md` (revised in lock-step with this overlay; specific sections updated per Changelog below).
- Postmortem: `.omc/SESSION_HANDOFF.md` (device fingerprint flag, day 5 of 7–14d cooloff).

This is an overlay, not a rewrite. It adds structured deliberation on top of the existing plan.

---

## 1. RALPLAN-DR summary

### 1.1 Principles (5)

P1. **No IG-CDN or instagram.com traffic from the backend.** Bytes only enter via the extension's authenticated `fetch()` in IG-tab context. Forced by the device-fingerprint flag (`SESSION_HANDOFF.md`); applies to media, page HTML, GraphQL, *everything*.

P2. **Stealth > speed > feature breadth.** A multi-week initial crawl that completes is strictly preferable to a faster crawl that triggers a re-flag. Any decision that trades latency for stealth wins by default. (Spec §Constraints, plan §1 north star.)

P3. **Extension owns the network surface; backend owns durability.** Extension is responsible for enumeration, post extraction, media fetch, and pacing. Backend is responsible for storage, retry-orchestration metadata, and read APIs. Neither side reaches into the other's domain. (Spec §Goal "Division of responsibility.")

P4. **Append-only archive after discovery.** No reconcile, no unsave tracking, no full re-enumeration. Once a post is captured, it lives forever in the archive regardless of IG-side state. (Spec round 3, plan §1.)

P5. **Single-user, localhost-bound, no auth UI.** The system runs as one user on a Mac mini at `127.0.0.1`. The only secret-gated surface is the ingest endpoints; retry endpoints are loopback-only. No multi-tenancy, no user management. (Plan §5.1.)

### 1.2 Decision drivers (top 3, ranked)

D1. **Device fingerprint flag on the dev MBP is unrecoverable from backend.** This is the forcing function for the entire pivot. Any architecture that re-introduces backend → IG traffic re-loses. (Per `SESSION_HANDOFF.md`.)

D2. **MV3 service-worker eviction policy bounds what "long-running" means.** Chrome can evict an idle SW after ~30s; `chrome.alarms` resumes it but in-memory state is gone. Bursts of 180–900s need to survive eviction inside the burst window. Drives offscreen-document choice, drives backend-owned cursor, drives explicit tab-ownership tracking (Δ4).

D3. **Initial crawl duration is dominated by saved-post count and heat budget, not engineering throughput.** At the locked stealth pacing (one media in flight, jittered bursts, awake window, rest day) the system sustains roughly **~168 posts/day** end-to-end. The crawl is **1–6 weeks depending on saved-post count** (~1 week for <1500 posts, ~6 weeks for 7000+). We have wall-clock slack; we do not have a budget for elevated-volume signals that re-flag the account. Drives jitter ranges, rest-day rule, single-media-in-flight rule, and the choice to NOT introduce a DYI pre-seed path in v1 (see option (a) below).

### 1.3 Viable options for the core architectural question

**Question:** How do we ingest IG content from a flagged host where the backend cannot speak to instagram.com?

#### Option A — Chrome MV3 extension in user's personal profile *(chosen)*
**Pros:**
- Uses the user's real cookies, real Chrome version, real Blink fingerprint. Indistinguishable from organic browsing at the network layer.
- `fetch()` from extension context against IG CDN carries the auth cookie automatically. Media access is solved without re-implementing IG's auth state.
- Extensions are a first-class supported Chrome surface; no automation flags exposed in `navigator.webdriver` or DevTools-protocol headers.

**Cons:**
- MV3 service-worker eviction makes "long-running" coordination genuinely hard; needs offscreen documents, backend-owned cursor, and explicit tab-ownership tracking (already in plan §4.6, §4.8, and Δ4 below).
- IG layout changes invalidate content-script selectors without warning; no graceful recovery story (see Risk R5).
- Concurrency is bounded to one media at a time across the whole extension; throughput is fundamentally low (~168 posts/day at locked pacing).

#### Option B — Native macOS app + Safari Web Extension API
**Pros:**
- WebKit fingerprint, not Blink. Different fingerprint surface from the flagged MBP's Chrome.
- macOS-native: Keychain + launchd vs. chrome.storage + chrome.alarms.

**Cons:**
- Safari Web Extension API is a strict subset of MV3; offscreen documents are missing; service-worker semantics differ. Whole burst-orchestration design would need rework.
- Same Mac mini → same hardware fingerprint (canvas/WebGL via the same GPU). If IG's flag uses GPU-level signals, Safari doesn't help.
- Doubles the toolchain (Swift + xcodebuild + signing). Single-user self-hosted project doesn't justify it.
- User said "Chrome on the Mac mini" multiple times; switching browsers is a UX regression.

#### Option C — Playwright/Puppeteer in a persistent user profile (headful)
**Pros:**
- Real Chrome binary, real cookies (persistent profile).
- Programmatic control is dramatically easier than content-script + service-worker dance.

**Cons:**
- `navigator.webdriver === true` is the classic detection signal. Even with `--disable-blink-features=AutomationControlled`, CDP leaves fingerprintable traces. IG specifically checks for these.
- The user just got fingerprint-flagged on this machine. Adding any automation-detectable signal during cooloff is reckless.
- The spec explicitly lists this as a non-goal (Spec §Non-Goals).

#### Option D — Hybrid: extension scrapes shortcodes only, backend fetches media via residential proxy
**Pros:**
- Decouples slow shortcode-discovery from heavy media-fetch.
- Residential proxies present a "consumer IP" fingerprint.

**Cons:**
- Re-introduces backend → IG CDN traffic, which violates P1 directly. Cookies from IP A, requests from IP B = obvious anomaly.
- Residential-proxy markets are seedy; adds external dependency to a single-user self-hosted app.
- Signed CDN URLs expire fast; backend would need to coordinate URL freshness with extension anyway.

#### Option E — Manual export via Meta "Download Your Information"
**Pros:**
- Zero scraping risk. Officially supported. High-fidelity for saved posts.
- Could complement Option A as a **bulk historical seed** for very large backlogs.

**Cons:**
- Manual export is on Meta's cadence (24h–14d wait for the ZIP). Not a real-time archive.
- Format is JSON + media files in Meta's chosen schema; would require an importer regardless.
- Doesn't cover the watch-mode "catch new saves" use case at all.

#### Why A still wins (and why B–E are invalidated for v1)

- **B (Safari extension):** Same hardware fingerprint as the flagged Chrome (canvas/WebGL/GPU all come from the same Mac mini). Real protection would require a *different machine*, not a different browser on the same machine. Defer until we can also change machines.
- **C (Playwright):** Explicitly non-goal in spec; `navigator.webdriver` and CDP traces are exactly the signals IG already flagged us on. This option *worsens* the problem.
- **D (proxy):** Violates P1 directly. Cookie-IP mismatch is a stronger fingerprinting signal than the original.
- **E (DYI export):** Complementary, not replacement. **Considered for a "pre-seed" path (E3.5) to shorten the wall-clock for large backlogs; rejected for v1** on engineering-cost grounds — adding `POST /api/ingest/dyi-import` plus a Meta DYI JSON schema parser is non-trivial, the format drifts on Meta's release cadence, and the locked stealth pacing means enrichment + media still take 1–6 weeks regardless (DYI only short-cuts discovery, which is the cheap pass). Re-open in v2 if real-world saved-post counts exceed 7000 (Open Question O1).

A is selected with caveats captured in Risks R5 (layout), R6 (heat-detection), R7 (storage), and R8 (alert persistence).

---

## 2. Refined acceptance criteria

Auditing the 17 criteria in spec §Acceptance Criteria. Items 21 and 22 are net-new for this consensus overlay (not in spec; live here, not in spec).

1. **Extension installs unpacked, accepts secret via popup, round-trips state.** Keep as-is.
2. **Auth-watch detects logged-out state and triggers heartbeat with `state='logged_out'`; Telegram stub logs.** Refine: "Auth-watch detects logged-out within 30s of redirect to `/accounts/login/` or appearance of `<input name="username">` on `/saved/`; backend records `ingest_meta.last_logged_out_at` within 5s of heartbeat; `notify.telegram.alert` writes one JSONL line to `.omc/logs/alerts.log` with severity='critical' (per R8/§5.5)."
3. **Discovery scrolls top→bottom, captures shortcode + stable recency_rank, end-of-list at 5 stable heights.** Keep as-is.
4. **Each named collection separately enumerated; `post_collections` populated.** Keep as-is.
5. **Enrichment pulls oldest-first.** Refine: "Enrichment SELECT clause uses `ORDER BY recency_rank DESC LIMIT 1`; integration test asserts ranks 9, 8, 7, 6, 5 in order against a synthetic DB."
6. **Post-state machine: 3 auto-tries with 30min/2h/12h, hard 404 → lost + 7-day sanity recheck.** Keep as-is.
7. **Media-state machine: 3 tries same URL, then re-visit + 2 more.** Keep as-is.
8. **Media uploaded via multipart; backend re-hashes; sha-mismatch → 400.** Keep as-is.
9. **Dedup HEAD endpoint: 204 / 404.** Keep as-is.
10. **Watch-mode gate when `(enriched + lost) == total_discovered`.** Keep as-is.
11. **Watch mode top-peek every 12–24h.** Refine: "First top-peek scheduled within `uniform(12h, 24h)` of entering `watch` phase; verifiable via mocked-RNG unit test (see §5.6); only top 50 grid items scraped; new shortcodes prepend with `priority_target.reason='watch_peek'`."
12. **`POST /api/posts/:id/retry-page` resets to placeholder + signals extension.** Keep as-is.
13. **`POST /api/posts/:id/retry-media/:slide_idx` resets slide + queues re-visit.** Keep as-is.
14. **Frontend tile states (4 buckets).** Refine: "Per-tile aggregate `media_status ∈ {all_present, some_failed, none_present}` is **materialized on `posts` table** as `slides_total`, `slides_present`, `slides_failed` integer columns maintained by triggers on `post_media` write (see Δ3 below). Tile renders deterministically from `(posts.state, media_status)` with no per-slide fetches and no GROUP BY at query time."
15. **Single Retry button per card hits contextually-correct endpoint.** Keep as-is.
16. **No backend HTTP to instagram.com / cdninstagram.com / fbcdn.net.** Refine: "Static check (verified post-E0 cleanup): `! grep -RnE 'instagram\\.com|cdninstagram\\.com|fbcdn\\.net' backend/ --include='*.py'` returns no lines (excluding comments referring to client-side URLs by name and excluding test fixtures with `# allow:` markers). Dependency check: `pip list | grep -iE 'instagrapi|instaloader'` returns empty. The grep must be re-run *after* E0 has deleted `backend/ig_client/`, otherwise it surfaces legitimate references inside soon-to-be-removed B1/B2 code."
17. **After E0: no references to instagrapi/instaloader/scheduler/reconcile/ig_client/ingest.runner.** Keep as-is.
18. **All existing API tests pass + new endpoint tests pass.** Refine: "`pytest tests/ -v` exits 0; `cd frontend && pnpm tsc --noEmit` exits 0; `tests/integration/test_ingest_extension.py` covers all 11 endpoints with happy + error path each (22+ test functions)."
19. **Fake-IG fixture supports end-to-end smoke.** Refine: "`tests/fixtures/fake-ig/` serves under `python -m http.server 8080`; extension loaded in dev mode with `localhost:8080` in `host_permissions`; smoke completes discovery → enrichment → media upload → one tombstone + one media_failed in <10 minutes; retry click advances within one burst cycle."
20. **No automation warning after one week.** Refine: "Manual smoke verification at E7 with explicit checklist: (a) no `/accounts/login/` redirect during a 30min observation window of normal browsing on a separate Chrome tab, (b) no challenge modal on `/saved/` during the window, (c) backend log shows no `state='logged_out'` heartbeats in the 7-day window, (d) **no automated HTTP request against instagram.com from the dev Mac mini's terminal during the window** (corrects original §5.4 footgun). Documented as `.omc/verification/e7-checklist.md` authored at E7."
21. **(NEW) Per-burst heat metrics + throttling detection.** "Extension records per-burst metrics (`hydration_p50_ms`, `http_4xx_rate`, `login_redirects`) to `chrome.storage.local.burst_metrics` (rolling 7-burst window). Threshold violation per R6 fires `state='throttling_suspected'` heartbeat; backend pauses extension (next `/state` returns `phase='paused'`) and writes a JSONL line to `.omc/logs/alerts.log`. Frontend `IngestStatusCard` shows red banner. Verifiable: inject synthetic 4xx burst into fake-IG fixture, observe heartbeat + alert log line within one burst."
22. **(NEW) Storage exhaustion guard.** "Backend exposes `GET /api/ingest/status` with `media_disk_used_bytes` field. When `media_disk_used_bytes / MAX_MEDIA_GB > 0.80`, next heartbeat response sets phase=`paused` and writes `state='storage_low'` alert to `.omc/logs/alerts.log`. Frontend renders red banner. Verifiable: temporarily set `MAX_MEDIA_GB=0.001` in `.env`, restart backend, perform one media upload, observe heartbeat alert."

---

## 3. Implementation plan: deltas only

The implementation plan exists in detail at `.omc/plans/extension-pivot.md`. I will NOT rewrite it. Below is the 10-line batch summary and a set of precise deltas.

### 3.1 Batch sequence (10-line summary)

- **E0 (½ day):** Repo cleanup — delete `ig_client/`, `ingest/`, `scheduler/`, `api/auth.py`; strip `instagrapi`/`instaloader`; tests green.
- **E1 (1–2 days):** Backend endpoints — migrations 002/003 (wrapped in `BEGIN/COMMIT` per Δ1), `ingest_extension.py` with 11 endpoints, `from_upload.py`, telegram stub with JSONL alert log (per R8), tests.
- **E2 (1–2 days):** Extension scaffold — manifest, vite/@crxjs, popup with secret, `lib/*`, `background.ts` idle-only with explicit tab-ownership tracking (per Δ4), round-trip smoke.
- **E3 (2–3 days):** Discovery — `saved-grid.ts` Pass A + Pass B, recency_rank capture, phase transitions to `enrichment`-gate. End-of-E3 surfaces `total_discovered` to user via `IngestStatusCard` with honest 1–6 week throughput projection (per R3, D3).
- **E4 (3–4 days):** Enrichment + media — `post-detail.ts`, offscreen worker, both state machines, oldest-first ordering, per-burst metrics capture (per R6), media-disk telemetry (per R7), backend wiring.
- **E5 (1 day):** Watch + auth-watch + heat-watch — top-peek loop, logout detection, throttling-suspected detection, heartbeat schema with `metrics` field, JSONL alert log.
- **E6 (1 day):** Frontend tiles — tombstone, broken-image, skeleton, single Retry button per card (disabled with tooltip when `phase='logged_out'` per T3), `IngestStatusCard` with persistent red banners for `logged_out`/`throttling_suspected`/`storage_low`.
- **E7 (1 day attended):** Real-IG smoke — gated on Resume A cooloff; manual checklist per refined criterion 20; **no terminal-based HTTP probes against instagram.com from the dev Mac mini**.
- **E8 (½ day):** Telegram real wiring — `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, replace stub. JSONL alert log persists regardless (independent of E8).
- (Total: ~10–14 working days. The wall-clock for the actual archive is 1–6 weeks at the locked stealth pacing, independent of engineering days.)

### 3.2 Deltas to existing plan

#### Δ1. §5.3 migration 002 — atomic transaction + CHECK semantics (plan lines 425–456)

**Issue:** Original SQL had no transaction wrap. A partial failure (e.g., disk full between ALTER and CREATE INDEX) would leave the schema half-migrated. Also: SQLite `ALTER TABLE … ADD COLUMN … CHECK` is enforced on future writes only; existing rows accept the DEFAULT, which is in the allowed set — verified against SQLite 3.51.0 on dev.

**Action:** Wrap migration 002 in an explicit transaction. Full SQL lives in plan §5.3; the structural change is:

```sql
PRAGMA foreign_keys = ON;
BEGIN;
  -- existing ALTERs (recency_rank, state, retry_count, next_retry_at, ...)
  -- Δ3 (b) new ALTERs: slides_total, slides_present, slides_failed
  -- existing CREATE INDEXes
  -- Δ3 (b) three CREATE TRIGGERs on post_media INS/UPD/DEL
  --        maintaining posts.slides_{total,present,failed}
  --        (mirrors FTS trigger pattern already in 001_init.sql)
  INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'));
COMMIT;
```

Integration test (per §5.6): apply against a copy of `data/app.db`; assert no row loss; assert new columns + triggers exist; assert CHECK enforced on bad INSERT; **assert atomicity** by monkeypatching `cursor.execute` to raise on the third statement and verifying rollback leaves the schema unchanged.

#### Δ2. §4.2 phase machine — `priority_target` UX gap

**Issue:** Plan §4.9 says manual retry sets `priority_target`; §4.2 says bursts run every 30–180 min. Combined: user clicks Retry, waits up to 3 hours.

**Action:** When `ingest_meta.priority_target` is set, next state-poll causes `background.ts` to schedule an early burst within `uniform(60s, 300s)`. Cap: at most one early-burst per 30min window. Tile "Queued" badge shows "Queued — running in ~Xm". Adds ~10 lines to `background.ts` + one `priority_burst_at` field in `chrome.storage.local`.

#### Δ3. §6.1 tile aggregation — stored computed columns (replaces correlated subqueries)

**Issue:** Original Δ3 proposed three correlated subqueries in `/api/posts` list endpoint. At 5000+ rows this is O(N²) — fine for a single user but the trigger-maintained materialization is cheaper, matches the existing FTS trigger pattern in `001_init.sql`, and keeps the read path index-friendly.

**Action (option b chosen, recommended in must-fix item 6):** Store `slides_total`, `slides_present`, `slides_failed` directly on `posts` as `INTEGER NOT NULL DEFAULT 0`. Three triggers on `post_media` (INS/UPD/DEL) keep them current. See Δ1 SQL block above for the full trigger definitions.

`/api/posts` list endpoint then becomes:

```sql
SELECT p.*,
       CASE
         WHEN p.slides_present = 0 AND p.slides_total > 0  THEN 'none'
         WHEN p.slides_failed  > 0 AND p.slides_present > 0 THEN 'partial'
         WHEN p.slides_present = p.slides_total            THEN 'all'
         ELSE 'none'
       END AS media_status
FROM posts p
WHERE ...
```

No GROUP BY, no correlated subqueries, no per-row scan of `post_media`. Adds ~3 lines to `backend/api/posts.py` and the derived field in `frontend/src/api/types.ts`.

**Why option (b) over option (a) LEFT JOIN + GROUP BY:** at 5000+ posts the GROUP BY plan re-scans `post_media` on every list request (which is polled by the frontend). The trigger approach pays the cost once per write. Writes are bounded by extension pacing (~168 posts/day = trivial); reads are user-driven (potentially every 30s when the IngestPage is open). Optimize for the hot path.

#### Δ4. Tab ownership tracking (replaces ambient `chrome.tabs.query`)

**Issue:** Original §4.3 said "SW re-discovers via `chrome.tabs.query({url: 'https://www.instagram.com/*'})`". This is hostile: it claims any IG tab the user has open as an extension target, which violates principle of least surprise and can drive an automation tab into a manual-browsing tab mid-session.

**Action:** Explicit ownership tracking.

1. Every time the SW opens a tab via `chrome.tabs.create()`, it immediately writes:
   ```ts
   chrome.storage.local.extension_owned_tabs[tabId] = {
     role: 'saved-grid' | 'post-detail' | 'collection',
     createdAt: ISO,
   }
   ```
2. On SW resume after eviction, the SW iterates the stored `extension_owned_tabs` map. For each `tabId`:
   - `chrome.tabs.get(id)` confirms the tab still exists.
   - The URL is verified to still match the role (`/saved/*/` for saved-grid; `/p/<shortcode>/` for post-detail; `/saved/<col>/` for collection).
   - Tabs the user closed (`chrome.tabs.get` errors with "No tab with id") are pruned from the map; the SW recreates them as needed.
   - Tabs that exist but the URL no longer matches (user navigated away) are pruned and recreated.
3. **The SW NEVER `chrome.tabs.query`s against the IG host pattern at large.** User-opened IG tabs are invisible to the extension.

Survival table (revised from original Δ4):

| Work unit | Survives SW eviction? | Recovery mechanism |
|-----------|----------------------|-------------------|
| Phase + cursor (chrome.storage.local) | Yes | Always re-read on SW resume |
| Phase + cursor (in-memory) | No | Re-read from storage on `chrome.alarms.onAlarm` |
| Current burst window timing | No | Backend `/state` is authoritative; SW re-derives next slot |
| In-flight `fetch()` of media | No | Treated as `transient_fail`; retry on next burst |
| Blob being hashed in offscreen | Yes | Offscreen survives idle SW |
| **Tab IDs (extension-owned)** | **Yes (via `extension_owned_tabs` map)** | **Iterate map, validate each via `chrome.tabs.get`, prune+recreate as needed. NEVER `chrome.tabs.query` arbitrary IG tabs.** |
| **User-opened IG tabs** | **N/A (not extension-owned)** | **Never targeted, never modified, never read.** |

Adds ~40 lines to `background.ts` and one entry (`extension_owned_tabs: Record<number, {role, createdAt}>`) to `chrome.storage.local`. Updates plan §4.2 phase machine to enumerate these invariants.

#### Δ5. INGEST_SECRET rotation story

**Issue:** Plan creates `INGEST_SECRET` (entered via popup once, env var server-side) and never addresses leakage/rotation.

**Action:** Add to §5.1:
- Rotation: change `INGEST_SECRET` in `.env` → restart backend → existing extension calls 401 → popup shows "secret mismatch" banner → user re-enters new secret → resumes.
- Backend logs first 401 from missing-secret request; suppresses subsequent 401 logs for 5min.
- One-line `.env.example` entry: `INGEST_SECRET=change-me-32-byte-hex`.
- Manual operation only. Documented in `README.md` setup section (TODO at E2).
- Verified by §5.6 smoke test (rotate secret + restart + observe 401 + popup re-entry).

#### Δ6. Logged-out UX for Retry button (T3)

**Issue:** A user clicking Retry while the extension is `logged_out` would queue a `priority_target` that never runs (because the extension's `logged_out` phase halts all bursts). UX dead-end.

**Action:** When `/api/ingest/status` reports `phase = 'logged_out'`, the React UI **disables the per-tile Retry button** and renders a `title` tooltip on hover:

> "Extension is logged out of Instagram. Log in via Chrome to resume retries."

The persistent red banner (§3.2 Δ7 below) carries the same message at page-level. Polling cadence: existing `['ingest','status']` query at 30s, so the button re-enables within 30s of the user logging back in (auth-watch fires a heartbeat with `state='logged_in'` on re-detection of the grid). No new click handler logic; just a `disabled={status.phase === 'logged_out'}` prop on the existing button.

#### Δ7. JSONL alert persistence (independent of Telegram wiring)

**Issue:** Original plan put Telegram alerts behind a TODO stub. Between E5 (alerts start firing) and E8 (Telegram wired), the only record is the backend log — easy to miss.

**Action:** Update `backend/notify/telegram.py` stub to always append one JSONL line per alert to `.omc/logs/alerts.log` (record = `{ts, severity, message}`). Full code lives in plan §7. Frontend `IngestStatusCard` reads `last_logged_out_at`, `last_throttling_at`, `last_storage_low_at` from `/api/ingest/status` and renders a persistent red banner with actionable text. Makes E5 → E8 a usable window without Telegram; gives a forensic audit trail regardless of delivery status.

Frontend renders only the **single highest-precedence banner** per render cycle, per the precedence rule in plan §4.2: `logged_out > storage_low > throttling_suspected > paused`. All `last_*_at` timestamps remain queryable via `/api/ingest/status` for forensic UI (Ingest page detail view).

---

## 4. Risks (top 8) + mitigations

### R1. MV3 service-worker eviction interrupts long-running bursts
- **Probability:** Medium-high. Chrome evicts SWs aggressively (~30s idle). Bursts span 180–900s but most of that is sleeps; SW will go idle between scroll events.
- **Blast radius:** Bounded. Plan §4.6 puts blob work in offscreen docs; cursor lives in `chrome.storage.local`; tab IDs now tracked explicitly (Δ4). A mid-burst eviction = "lose one scroll position, restart from cursor."
- **Mitigation:** Δ4 (explicit tab-ownership + survival map). Backend-owned cursor is source of truth; SW state is cache.
- **Escalation trigger:** If E4 smoke shows SW eviction *during* in-flight media upload more than once per 100 uploads, switch upload path to chunked fetch initiated from offscreen with `keepalive: true`.

### R2. Migration 002 ALTER TABLE atomicity
- **Probability:** Low. SQLite 3.51.0 handles `ALTER TABLE ADD COLUMN … CHECK` correctly with DEFAULT on existing rows.
- **Blast radius:** Low. Worst case: mid-migration failure leaves half-applied schema → revert from `data/app.db.bak`.
- **Mitigation:** Δ1 wraps all ALTER + CREATE INDEX + CREATE TRIGGER statements in `BEGIN/COMMIT`. Integration test (§5.6) deliberately raises mid-migration and verifies rollback.
- **Escalation trigger:** If integration test fails on dev DB copy, fall back to SQLite "12-step ALTER" (create new table → INSERT … SELECT → DROP old → RENAME).

### R3. Single concurrency token bottlenecks throughput on large backlog
- **Probability:** Certain at scale. The locked stealth pacing yields ~168 posts/day end-to-end. A 1500-post backlog = ~1 week; a 7000-post backlog = ~6 weeks.
- **Blast radius:** UX expectations, not safety. **Timeline reconciliation (per must-fix item 2, option a chosen):** the spec's "1–2 week initial crawl" framing is widened to **"1–6 weeks depending on saved-post count"** consistently across D3, AC#20, E3 batch, and below.
- **Mitigation:** At E3 completion (Pass A discovery), surface `total_discovered` to user with honest projection: `weeks_estimate = total_discovered / 168 / 7`. Render the projection in `IngestStatusCard`. Do NOT increase parallelism.
- **Escalation trigger:** If user explicitly asks for faster, the negotiation is "add second extension on a different machine," NOT "bump concurrency." DYI pre-seed is **deferred to v2** (Open Question O1) on engineering-cost grounds.

### R4. No coexistence logic with human IG browsing (round 2 decision)
- **Probability:** Low for re-flag, Medium for weird signal.
- **Blast radius:** Theoretical. Cookies + IP identical between user browsing and extension; no obvious anomaly.
- **Mitigation:** Accept per spec round 2. Add "Pause for X hours" button in `IngestPage` (E6) as escape hatch.
- **Escalation trigger:** Any automation warning at E7 → first remediation is "add `chrome.idle.queryState` coexistence logic" (un-do round 2 simplification). Cheapest re-add, no schema changes.

### R5. IG layout changes invalidate selectors mid-crawl — no recovery story
- **Probability:** Medium. IG ships frontend changes weekly.
- **Blast radius:** Total stall. If selectors fail, discovery silently produces zero rows.
- **Mitigation:** Discovery canary in `content/saved-grid.ts`: after 60s of scrolling with zero new shortcodes AND changing scroll-height, fire `state='selectors_broken'`. Same pattern in `post-detail.ts` (`state='extraction_failed'`). Backend logs to `ingest_meta.layout_warning_at`; `IngestStatusCard` surfaces it; JSONL alert (per Δ7).
- **Escalation trigger:** Any `selectors_broken` or `extraction_failed` heartbeat → halt automatic phase advancement; require manual ack via popup.

### R6. Heat detection — IG silently throttles us before issuing a hard re-flag (NEW)
- **Probability:** Medium. The original device flag came without warning; the system needs to detect ratcheting throttling pre-emptively.
- **Triggers (any one):**
  - (a) Median hydration time on `/p/<shortcode>/` > 1.5× the 7-burst rolling baseline.
  - (b) HTTP 4xx rate on `cdninstagram.com` media URLs > 5% over 7-burst baseline. Known limitation deferred to E7 tuning: if baseline drifts > 0.02 over time, trigger (b) silently disarms. Mitigation considered at E7: change predicate to `OR http_4xx_rate > 3 × baseline.http_4xx_rate AND http_4xx_rate > 0.05`. Tracked as open question O5.
  - (c) Any redirect to `/accounts/login/` *mid-burst* (i.e., not at session start).
- **Blast radius:** High if missed (silent ratchet to a re-flag). Bounded if caught (graceful pause).
- **Mitigation:** Extension writes per-burst metrics to `chrome.storage.local.burst_metrics` (rolling 7-burst window). Each post-burst evaluation checks the three triggers against the rolling baseline. Trigger fires `state='throttling_suspected'` heartbeat with the `metrics` payload (see §4.7 update). Backend's next `/state` response sets `phase='paused'`. JSONL alert appended (Δ7). `IngestStatusCard` shows red banner. User manually unpauses via popup once they've assessed the situation.
- **Escalation trigger:** A `throttling_suspected` heartbeat that recurs within 24h of a manual unpause → auto-extend the pause to 7 days (cooloff equivalent), with the user re-acknowledging via popup.

### R7. Storage exhaustion (NEW)
- **Probability:** Low at <2000-post archives, Medium at 5000+ (avg slide count × avg slide size × 5000 can exceed default disk budgets).
- **Triggers:** `data/media/` directory size exceeds 80% of `MAX_MEDIA_GB` env var (default 50GB).
- **Blast radius:** Degraded operation, not data loss. Enrichment pauses; existing data stays readable.
- **Mitigation:** Backend exposes `media_disk_used_bytes` in `/api/ingest/status` (computed via cached `du` every 5min). On threshold breach, next heartbeat response sets `phase='paused'`, writes `state='storage_low'` JSONL alert. `IngestStatusCard` shows red banner. User decides: free disk, bump `MAX_MEDIA_GB`, or accept the cap. **Recovery model: manual-ack-via-popup** (matching R6's design). Auto-resume on disk-usage drop is explicitly rejected to avoid oscillation between 70% and 80% thresholds (would re-page the user repeatedly). When user clicks "Resume" in popup after addressing disk, popup POSTs `/api/ingest/extension/resume` which clears `ingest_meta.last_phase` if it was `storage_low`.
- **Escalation trigger:** Disk at 95% → also pause read endpoints to prevent further state changes (prevents partial-write corruption on a full filesystem).
- **Config:** Add `MAX_MEDIA_GB=50` to `.env.example`.

### R8. Alert blackout between E5 and E8 (NEW)
- **Probability:** Certain (by design — Telegram wiring is deferred to E8).
- **Blast radius:** Medium. Between E5 (alerts start firing) and E8 (Telegram delivers them), without persistence the user has no way to know an alert fired except by scraping the backend log.
- **Mitigation:** Δ7 above. JSONL alert log + `IngestStatusCard` persistent red banner using `last_logged_out_at` / `last_throttling_at` / `last_storage_low_at` fields on `/api/ingest/status`.
- **Escalation trigger:** N/A — this is mitigation, not active risk monitoring.

---

## 5. Verification protocol

Concrete commands keyed to the (refined) acceptance criteria.

### 5.1 Backend / repo cleanliness (criteria 16, 17, 18)

```bash
# E0 completion check (criteria 16/17 only meaningful AFTER E0 deletes legacy code)
cd /Users/genehan/projects/claudehome-projects/gene-mini_suite/instagram-logger
! grep -RnE 'instagrapi|instaloader|ig_client|ingest\.runner|scheduler|reconcile' \
    backend/ tests/ pyproject.toml justfile --include='*.py' --include='*.toml' --include='justfile'

# Backend network-surface check (refined criterion 16, verified post-E0)
! grep -RnE 'instagram\.com|cdninstagram\.com|fbcdn\.net' backend/ --include='*.py'

# Dependency check
pip list --format=columns | grep -iE 'instagrapi|instaloader' && echo "FAIL: deps remain" || echo "OK"

# All tests + typecheck (refined criterion 18)
pytest tests/ -v
cd frontend && pnpm tsc --noEmit
```

### 5.2 Backend endpoint contracts (criteria 1–13, 21, 22)

```bash
pytest tests/integration/test_ingest_extension.py -v   # ≥22 functions
pytest tests/integration/test_migrations.py::test_002_atomic_rollback -v
pytest tests/integration/test_ingest_extension.py::test_state_returns_oldest_first -v
pytest tests/integration/test_ingest_extension.py::test_throttling_suspected_pauses_extension -v
pytest tests/integration/test_ingest_extension.py::test_storage_low_pauses_extension -v
```

### 5.3 Extension behavior against fake-IG (refined criterion 19, 21)

```bash
cd extension && pnpm install && pnpm build:dev
( cd tests/fixtures/fake-ig && python -m http.server 8080 ) &
FAKE_PID=$!
# Load extension manually in Chrome (chrome://extensions → load unpacked → extension/dist)
# Run discovery → enrichment → media smoke; observe backend DB:
sqlite3 data/app.db "SELECT state, COUNT(*) FROM posts GROUP BY state"
sqlite3 data/app.db "SELECT state, COUNT(*) FROM post_media GROUP BY state"
# Heat-detection smoke (criterion 21): fake-IG returns 4xx for 30% of media URLs in burst window.
# Expected: a heartbeat with state='throttling_suspected' and a JSONL line in .omc/logs/alerts.log.
tail -n 3 .omc/logs/alerts.log
kill $FAKE_PID
```

### 5.4 Real-IG smoke (refined criterion 20, gated on Resume A cooloff)

Author `.omc/verification/e7-checklist.md` at E7 time. The checklist covers:
- 30-min observation window of normal browsing in a separate Chrome tab.
- Open `instagram.com/<you>/saved/all-posts/` in a normal Chrome tab; **visually** confirm no `/accounts/login/` redirect and no challenge modal.
- Check backend log: `! grep "state.*logged_out" .omc/logs/backend.log` AND `! test -s .omc/logs/alerts.log` (or examine new entries since E7 start).
- **DO NOT run any automated HTTP request against `instagram.com` from the dev Mac mini's terminal during this window.** Specifically, no `curl https://www.instagram.com/...` — that would re-trip the device flag we are trying to recover from.

### 5.5 Frontend tile state (refined criterion 14)

```bash
cd frontend
pnpm test src/components/PostTile.test.tsx
# Expected: four rendering snapshots — normal, partial-failed, lost-tombstone, skeleton.
# Plus: Retry button disabled when status.phase === 'logged_out' (Δ6).
```

### 5.6 Verification gaps closed (new section)

The following gaps surfaced in iteration-1 review need explicit coverage:

| Acceptance criterion / Δ | Test | Type |
|--------------------------|------|------|
| AC#11 top-peek jitter window | `tests/unit/test_jitter.py::test_top_peek_window` with seeded RNG; assert next_peek_at ∈ `[now+12h, now+24h]` | Unit |
| Δ4 tab-ownership invariant | Integration test: create one extension-owned tab + one user-owned fixture tab; trigger simulated SW eviction; assert SW only drives the extension-owned tab and never `chrome.tabs.query`s for the other | Integration |
| Δ5 INGEST_SECRET rotation | Smoke: change `.env` `INGEST_SECRET` → restart backend → assert extension's old-secret request gets 401 → assert popup re-entry path sets new secret and resumes 200s | Smoke |
| Δ1 migration 002 atomicity | `tests/integration/test_migrations.py::test_002_atomic_rollback`: monkeypatch `cursor.execute` to raise on the third statement; assert post-migration schema matches pre-migration schema (no half-applied state) | Integration |
| AC#21 heat detection | Inject 4xx burst into fake-IG; observe `state='throttling_suspected'` heartbeat + JSONL alert + extension `phase='paused'` within one burst cycle. Smoke fixture sets `chrome.storage.local.test_mode_skip_warmup = true` before injecting the bad burst, per plan §4.3 warm-up bypass. | Integration (extension-driven) |
| AC#22 storage low | Set `MAX_MEDIA_GB=0.001`; restart backend; perform one media upload; observe `state='storage_low'` heartbeat + JSONL alert | Smoke |
| AC#22 storage_low recovery | `tests/integration/test_storage_low_recovery.py`: set `MAX_MEDIA_GB=0.001`, upload one media file, assert heartbeat shows `state='storage_low'` and `phase='paused'`; delete the test file (free disk); call popup resume endpoint (`POST /api/ingest/extension/resume`); assert `phase` returns to prior active phase (e.g., `enrichment`) | Integration |

---

## 6. Plan delivery confidence

This iteration-2 consensus pass closes **11 substantive gaps** raised by Architect (REVISE) and Critic (ITERATE) in iteration 1. The headline changes are:

- **Heat detection (R6, AC#21)** turns the system from "scrape until banned" into "scrape until first warning sign, then pause." This is the most consequential addition; without it, we re-burn the device flag silently. The trigger thresholds (1.5× hydration baseline, >5% 4xx rate, mid-burst login redirect) are conservative enough to false-positive-rather-than-false-negative.
- **Timeline honesty (D3, AC#20, R3)** widens the 1–2 week framing to 1–6 weeks across the document. The arithmetic at ~168 posts/day forced this; pretending otherwise would set up the user for a "why isn't it done yet" conversation in week 3.
- **Tab ownership (Δ4)** removes a hostile design — the SW no longer claims arbitrary user IG tabs.
- **Atomic migration + materialized aggregates (Δ1, Δ3)** make the schema change both safer (transaction-wrapped) and faster at scale (no GROUP BY at read time).
- **JSONL alert persistence (R8, Δ7)** keeps the system observable between E5 and E8 without requiring Telegram.
- **Verification footgun fix (§5.4)** removes the `curl instagram.com` line that would re-trip the device flag during the E7 manual check.

I am still NOT recommending escalation to DELIBERATE mode. The system remains localhost-only with git/DB rollback, single user, no production traffic. A pre-mortem + expanded test plan is process theater here. The 11 must-fix items are all in scope of SHORT mode.

---

## 7. ADR (Architecture Decision Record)

**Decision:** Build a Chrome MV3 extension in the user's personal profile to drive saved-post archival. Backend stays at 127.0.0.1, owns durable storage and read APIs, receives bytes from the extension via secret-gated multipart upload. No backend → instagram.com or backend → cdninstagram.com traffic, ever.

**Drivers:**
- D1: Device fingerprint flag on dev MBP makes backend→IG traffic structurally unsafe.
- D2: MV3 SW eviction policy bounds long-running coordination; offscreen docs + backend cursor + explicit tab-ownership compensate.
- D3: Crawl wall-clock is 1–6 weeks at locked stealth pacing; throughput is bounded by heat budget, not engineering.

**Alternatives considered:**
- B (Safari extension on same Mac mini): same hardware fingerprint; doesn't help.
- C (Playwright/Puppeteer): `navigator.webdriver` is the detection signal we already failed on.
- D (residential-proxy backend fetch): violates P1; cookie-IP mismatch is louder than the original signal.
- E (Meta DYI export): complementary, not replacement. Considered as E3.5 pre-seed; rejected for v1 on engineering-cost grounds (new endpoint + DYI JSON parser + Meta-cadence dependency). Re-open in v2 if saved-post counts > 7000.

**Why chosen:**
A is the only option that preserves all five principles and satisfies the watch-mode requirement. The cons (MV3 eviction, selector fragility, low throughput) are all mitigable in-design (Δ4, R5, R3) and the residual risk is bounded to "stall and notify" rather than "silently lose data" once R5 + R6 canaries are in place.

**Consequences:**
- Initial crawl is 1–6 weeks (honest projection per D3).
- Extension code is the weakest surface — must be maintained in lock-step with IG layout changes (R5 mitigation acknowledged).
- Single-user only. Multi-user would require auth UI, per-user secret, per-user data partitioning — all explicitly out of scope.
- Storage scales linearly with media; R7 + AC#22 enforce the cap.
- Heat-detection (R6 + AC#21) is the new operational invariant; the system must pause and notify before re-flagging, not after.

**Follow-ups (open questions):**
- O1: Re-evaluate DYI pre-seed (option (b) from must-fix item 2) for v2 if saved-post count is empirically > 7000 OR if the user explicitly accepts the engineering cost.
- O2: Re-evaluate single-concurrency token at v1 → v1.1 if throughput at scale proves intolerable AND no heat signals fire after 30 days at locked pacing.
- O3 (from plan §11): Tab management granularity — one shared tab vs. three dedicated tabs. Δ4 currently assumes three dedicated; re-evaluate at E2.
- O4 (from plan §11): Rest-day weighting (uniform vs. Sun/Mon-biased).
- O5 (from plan §11): Video extraction for DASH/HLS streams (v1 marks as `media_failed`).

---

## 8. Changelog (iteration 2)

Listing all 11 must-fix items + how each was applied.

1. **R6 heat detection** — Applied. Added §4 R6 with three triggers, §2 AC#21, §3.2 references to `chrome.storage.local.burst_metrics`, §4.7 heartbeat schema extension (now includes `metrics: {hydration_p50_ms, http_4xx_rate, login_redirects}`). Verification entry added to §5.6.
2. **Timeline reconciliation** — Applied **option (a) — widen to "1–6 weeks"**. Updated D3, AC#20, R3 mitigation, §3.1 batch sequence. Rationale: option (b) E3.5 pre-seed requires a new endpoint + Meta DYI JSON parser, expanding v1 scope materially; the locked stealth pacing means enrichment+media still take 1–6 weeks regardless of how discovery is seeded, so the pre-seed only short-cuts the cheap pass. Re-open as Open Question O1 in v2.
3. **Δ4 tab ownership marker** — Applied. Replaced ambient `chrome.tabs.query` with explicit `extension_owned_tabs` map in `chrome.storage.local`. Survival table revised. Plan §4.2 updated to reference these invariants.
4. **R7 storage exhaustion** — Applied. Added §4 R7, §2 AC#22, `MAX_MEDIA_GB=50` env entry, `media_disk_used_bytes` field on `/api/ingest/status`. Verification entry added to §5.6.
5. **R8 alert persistence (Δ7)** — Applied. Updated `backend/notify/telegram.py` stub to JSONL-append to `.omc/logs/alerts.log`. Plan §7 updated. Frontend `IngestStatusCard` reads `last_*_at` fields and renders persistent red banners.
6. **Δ3 query rewrite** — Applied **option (b) — stored computed columns + triggers**. `posts.slides_{total,present,failed}` maintained by three triggers on `post_media` INS/UPD/DEL. Replaces three correlated subqueries. SQL embedded in Δ1 transaction.
7. **T3 logged-out retry UX (Δ6)** — Applied **disabled-button variant** with hover tooltip. Reuses existing `['ingest','status']` 30s poll for re-enablement. No new state machine.
8. **§5.4 verification footgun** — Applied. Removed `curl https://www.instagram.com/api/v1/users/web_profile_info/?username=<self>`. Replaced with visual confirmation in a normal Chrome tab + explicit "no terminal HTTP" prohibition.
9. **Migration 002 transaction wrap** — Applied. Δ1 SQL wrapped in `PRAGMA foreign_keys = ON; BEGIN; ... COMMIT;`. Integration test (§5.6) added for atomic rollback.
10. **AC#16 post-E0 qualifier** — Applied. Refined criterion 16 now states "(verified post-E0 cleanup)" and explains why the grep would false-positive against B1/B2 code if run before E0.
11. **Verification gaps (§5.6)** — Applied. Added new §5.6 covering AC#11 (mocked-RNG jitter), Δ4 (tab-ownership integration test), Δ5 (INGEST_SECRET rotation smoke), Δ1 (migration atomicity), AC#21 (heat detection), AC#22 (storage low).

No items omitted.

- **Architect N1 (FTS `posts_au` trigger cascade guard) — evaluated and rejected.** The existing trigger in `backend/db/migrations/001_init.sql:62` is already declared `AFTER UPDATE OF caption, author_username_denorm ON posts`, which is column-gated by SQLite's `UPDATE OF` syntax. It does NOT fire when `slides_total`/`slides_present`/`slides_failed` are updated by the new aggregate-maintenance triggers. No change required.

---

**Files written in iteration 2:**
- `/Users/genehan/projects/claudehome-projects/gene-mini_suite/instagram-logger/.omc/plans/extension-pivot-consensus.md` (this file, revised in place)
- `/Users/genehan/projects/claudehome-projects/gene-mini_suite/instagram-logger/.omc/plans/extension-pivot.md` (updated §4.2, §4.3, §4.7, §4.9, §5.3, §7, §11 per must-fix items 1, 3, 5, 7, 9)

**Files unchanged:** `.omc/specs/deep-interview-extension-pivot.md` (the spec is the contract).

---

## ADR — Architecture Decision Record (consensus output)

**Date:** 2026-05-12
**Iterations:** 2 / 5 (max)
**Final verdict:** Critic APPROVE with 5 inline improvements (this document)

### Decision

Adopt the revised consensus overlay as the canonical planning document for the extension pivot. The implementation plan at `.omc/plans/extension-pivot.md` is the working specification; this consensus overlay is the principled-deliberation companion.

### Drivers

1. **Device fingerprint flag on dev MBP** makes any Python backend → IG traffic unrecoverable until cooloff or migration to a different host. Forcing function for P1 (no backend → IG traffic) and the entire extension pivot.
2. **MV3 service-worker eviction policy** bounds what "long-running" can mean at the extension layer; the design must survive the SW being killed during multi-hour bursts. Drives §4 architecture choices (offscreen document, tab-ownership map, alarm-driven resume).
3. **Stealth budget under one-media-at-a-time concurrency token**, applied to 5000+ saved posts × ~3 slides each, drives a 1–6 week wall-clock crawl envelope (option (a) chosen). Speed is explicitly subordinated to fingerprint safety.

### Alternatives considered

| Option | Status | Why rejected (or deferred) |
|--------|--------|---------------------------|
| A. Chrome MV3 extension | **Chosen** | Best fingerprint-equivalence to real user browsing; cookies + headers come for free; spec-non-goal aligns. |
| B. Safari Web Extension API | Rejected | Doubles toolchain for marginal gain; canvas/WebGL fingerprint plausibly differs but not verified; user committed to Chrome. |
| C. Playwright/Puppeteer persistent context | Rejected | Spec non-goal explicitly bans headless/automated browsers; `navigator.webdriver` and CDP traces remain even with stealth flags. |
| D. Hybrid: extension shortcodes + backend fetch via residential proxy | Rejected | Violates P1 (no backend → IG traffic); proxy quality is a moving target; adds external dependency for a single-user system. |
| E. Meta DYI export pre-seed | Deferred (O1) | Complementary, not replacement: DYI saves are shortcode-only; media bytes still flow through the extension. If E3 projects > 3 weeks, consider as opt-in pre-seed; otherwise not v1 scope. |

### Why MV3 wins

Of the 5 options, only A:
- Operates in the user's real authenticated browser context, so cookies and request headers are indistinguishable from manual browsing.
- Can fetch media via authenticated `fetch(url, {credentials:'include'})` with the page-context referer and CORS behavior.
- Avoids spec-banned automation tooling (Playwright/Puppeteer).
- Carries no external network dependency (no proxy provider).
- Preserves the locked decision that the backend never touches IG.

The MV3 SW-eviction concern is real but mitigated structurally: offscreen documents survive SW evictions for in-flight blob work; content scripts in the IG tab survive SW evictions for the scroll/extract loop; the tab-ownership map (Δ4) ensures resume hits only extension-owned tabs.

### Consequences

**Positive**
- P1 invariant cleanly preserved: zero backend → IG traffic.
- Failure modes localized to the extension layer (which is restartable and inspectable via Chrome devtools).
- UI tile states (tombstone, broken-image, skeleton, normal) provide a complete user-visible failure surface with the manual Retry button as escape valve.
- Heat-detection R6 + storage R7 + alert-log R8 close the previously-blind ratchet path that caused the original Python pivot.

**Negative**
- Wall-clock 1–6 weeks for backlog crawl is slow vs. an unconstrained automated solution; user acceptance documented in `deep-interview-extension-pivot.md` round 3.
- Extension code is new surface area (no prior repo precedent for MV3); toolchain (vite-crx-plugin, MV3 lint) adds ~1 day to E2.
- Per-tile aggregate via stored columns + triggers (Δ3 option b) adds 3 triggers to schema; net-positive for grid query latency but adds migration complexity.
- AC#20 ("no automation warning after one week") remains manually verified; full CI gating is not possible against real IG without re-tripping the flag we're recovering from.

### Follow-ups

1. **Open question O1** — DYI export pre-seed. Re-evaluate at E3 completion if `total_discovered > 7000` (or projected wall-clock > 3 weeks).
2. **Open question O2** — Multi-machine scale-out. Same DB, two extension instances on two different physical machines, partition the enrichment queue. Deferred to v2.
3. **Open question O3** — IG layout-change recovery automation. Currently `selectors_broken` halts and waits for manual ack; could escalate to auto-pull-latest-selectors from a versioned remote config. Deferred to v2.
4. **Open question O4** — Telegram bot real wiring (E8). Gated on E7 success.
5. **Open question O5** — R6 trigger (b) baseline-drift dead-zone (raised by Critic iter-2). Tune at E7 against real-IG observations: replace fixed threshold with multiplier `OR http_4xx_rate > 3 × baseline.http_4xx_rate AND http_4xx_rate > 0.05`.

### State of artifacts post-consensus

| Artifact | Path | Status |
|----------|------|--------|
| Spec | `.omc/specs/deep-interview-extension-pivot.md` | Unchanged (contract from /deep-interview round 4, 9% ambiguity) |
| Plan | `.omc/plans/extension-pivot.md` | Revised (~788 lines + this iteration's inline improvements) |
| Consensus | `.omc/plans/extension-pivot-consensus.md` | Final (this document) |

Ready for execution — no further consensus iterations needed.
