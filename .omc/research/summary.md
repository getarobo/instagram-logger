# Phase 1 — Research Summary

Three parallel doc-research lanes (Instagram client lib · React frontend · media storage). Results below; recommendations bolded.

---

## 1. Instagram Python client library — **instagrapi**

| | instagrapi | instaloader | Cookie + raw API |
|---|---|---|---|
| Saved posts feed | yes | yes (`Profile.get_saved_posts`) | yes (`/api/v1/feed/saved/`) |
| **Named collections enumeration** | **yes — `collections()`, `collection_medias_by_name()`** | **NO — open issues since 2019, refused as out-of-scope** | yes, but you maintain endpoint paths yourself |
| Persistent session | `dump_settings/load_settings` JSON, preserves device fingerprint | `load_session_from_file` | manually paste cookie; expires silently |
| 2FA / checkpoint | `challenge_resolve()` automated for SMS/email; manual fallback | manual only | manual only |
| Carousel + video URLs | `Media.resources`, `Media.video_url` | yes, but downloader-oriented | raw JSON, you parse |
| Maintenance | active (releases through May 2025) | active (v4.15.1 Mar 2026) | n/a |
| Ban risk for our use | moderate; mitigations: low cadence, dedicated non-primary account, persistent device | lower (web endpoints) but feature gap is disqualifying | low risk of ban; high risk of silent breakage |

**Decision: instagrapi.** It is the only option that natively enumerates named collections — a hard requirement from Phase 0. instaloader is disqualified by missing collections; raw cookie is a maintenance trap.

**Mitigations baked into design (updated after Phase 1 gate):**
- Archiver logs in as the user's PRIMARY Instagram account — corrected from earlier draft after confirming saved posts are private to the account that saved them, so a secondary account cannot see the user's saves.
- **Poll cadence: 24h** with randomized jitter (e.g., random offset within a 2h window) — user explicitly chose 24h to minimize automation footprint.
- Persist `settings.json` (device fingerprint + session cookies) across restarts so re-login is rare.
- Mobile-app-mimicking User-Agent and consistent device-id (instagrapi default).
- 2FA / checkpoint fallback: surface a clear "needs human" state in the web UI rather than silently failing.
- Easy "pause sync" toggle in the UI so user can stop polling immediately if anything looks off.

**Sources:** [instagrapi collections](https://subzeroid.github.io/instagrapi/usage-guide/collection.html), [best practices](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html), [instaloader issue #1172](https://github.com/instaloader/instaloader/issues/1172)

---

## 2. React frontend stack — **Vite + shadcn/ui + YARL + TanStack Query**

| Decision axis | Pick | Why |
|---|---|---|
| Build tool | **Vite 6 + React 19 + TS** | Pure SPA → static files; no SSR coupling with FastAPI; smaller bundle than Next |
| Styling | **Tailwind v4 + shadcn/ui** | Radix Dialog ready-made for post modal; Tailwind v4 has shadcn full support since Feb 2025; `grid grid-cols-3 gap-1` reproduces IG saved grid exactly |
| Lightbox / carousel / video | **yet-another-react-lightbox** | Only React lightbox shipping first-party plugins for Video, Captions, Thumbnails, Counter, Fullscreen — multi-slide carousel is its native data model |
| Data fetching | **TanStack Query v5** | Devtools, stale-time control, query-key composition for search/filter; worth the 13 KB over SWR |
| Virtualization | **none initially**; add `react-virtuoso` if > ~1000 posts feel slow | YAGNI; image grid lazy-load handles hundreds fine |
| State | TanStack Query cache + URL params for filters | No need for Redux/Zustand at this scale |

**Sources:** [shadcn/ui Tailwind v4](https://ui.shadcn.com/docs/tailwind-v4), [YARL plugins](https://yet-another-react-lightbox.com/plugins), [TanStack Query v5](https://tanstack.com/query/v5/docs/framework/react/comparison)

---

## 3. Media storage — **filesystem, content-addressed, dedup via sha256**

**On-disk layout**
```
data/
  media/
    ab/abcdef…<ext>          # originals, sha256-named, 2-char shard
  thumbnails/
    ab/abcdef…jpg            # IG CDN low-res variants, same shard scheme
```

**Schema (media-side only)**
```
media_files
  sha256          TEXT PRIMARY KEY
  file_path       TEXT NOT NULL          -- relative to data/
  mime_type       TEXT
  file_size_bytes INTEGER
  width / height  INTEGER
  duration_seconds REAL                  -- NULL for images
  fetched_at      TEXT (ISO8601)

post_media
  id              INTEGER PRIMARY KEY
  post_id         TEXT NOT NULL REFERENCES posts(id)
  media_sha256    TEXT NOT NULL REFERENCES media_files(sha256)
  thumbnail_sha256 TEXT REFERENCES media_files(sha256)
  carousel_index  INTEGER NOT NULL DEFAULT 0
  media_type      TEXT                   -- 'image' | 'video'
```

**Key decisions:**
- **Filesystem, not BLOBs.** SQLite docs themselves say >100 KB belongs on disk — IG media is hundreds of KB to tens of MB. ([source](https://www.sqlite.org/intern-v-extern-blob.html))
- **Dedup by sha256** — same media reposted, or same post in multiple collections, costs zero extra disk. Reference-count check on delete.
- **Never transcode originals.** Personal archive value is bit-exact preservation.
- **Carousel = one row per slide**, indexed by `carousel_index`. JSON arrays in posts row would lose queryability.
- **Thumbnails: store the IG-CDN variant as-is** at ingest. Zero CPU, zero Pillow dependency. Local copy is mandatory because CDN URLs rotate.

**macOS gotchas baked into setup:**
- Drop `.metadata_never_index` at the data root → Spotlight skips it
- `tmutil addexclusion data/media` → Time Machine doesn't balloon
- Optionally use `cp -c` (APFS reflink) for any internal media moves

**Sources:** [SQLite intern vs extern blob](https://www.sqlite.org/intern-v-extern-blob.html), [Git object storage](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

---

## Open questions still deferred to Phase 2 (Plan)

1. Sync scheduler — APScheduler vs simple asyncio loop; pick in plan based on launchd-vs-docker decision.
2. Re-auth UX detail — is it CLI-only (`python -m archiver login`) or in-UI banner with TOTP input?
3. launchd plist vs docker-compose for the always-on host — minor, decide in Phase 2.
4. Local web UI auth — keeping default of localhost-bind, no login. Will revisit if user wants tunnel/remote access.

## Assumptions called out
- User is willing to use a dedicated, non-primary Instagram account for this archiver. (If not, ban risk is materially higher; flag in Phase 2.)
- IG named-collections API surfaced by instagrapi remains stable; if Meta breaks it, we degrade to "All Posts" only and continue.
- Single-user, localhost-only, no mobile/PWA in v1 — confirmed in brief.
