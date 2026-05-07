# Instagram Saved-Posts Logger — Brief

## One-liner
A self-hosted, always-on service that continuously archives every post saved on a single Instagram account — including which named collections each post belongs to — into a local database with downloaded media, and serves an Instagram-like web UI for browsing the archive.

## Problem
Instagram's "Saved" area is a fragile bookmarking system: posts disappear when the original author deletes them, goes private, or gets banned; collections get reorganized; there is no search, no export, and no permanent personal archive. The user wants a durable, queryable, browsable copy that outlives Instagram.

## Primary user
Single operator (the user) running it on their own Mac for their own account. Not multi-tenant. Not public.

## Success criteria
1. Server runs continuously on the local Mac (launchd or docker), survives reboots, and resumes sync without manual re-auth in the common case.
2. Every currently-saved post is captured: post metadata + caption + author + media (images/videos, all carousel slides). Named collections are modeled as their own entity, and a post can belong to zero or many of them (mirrors Instagram exactly: every saved post implicitly sits in "All Posts" and may additionally appear in any number of named collections).
3. New saves on Instagram appear in the local DB on a configurable schedule (default ~hourly with jitter; tunable to dodge rate-limit signals).
4. Posts that get unsaved on Instagram or whose originals are deleted remain in the local archive, flagged `is_unsaved` / `is_source_deleted` and filterable in the UI.
5. Web UI (separate React frontend, talks to Python JSON API) provides:
   - "All Posts" three-column grid like instagram.com/USERNAME/saved
   - Per-collection grid views (one per named collection on Instagram)
   - Post detail modal: carousel, caption, author, save date, source status, list of named collections this post is in
   - Search by caption text and author handle
   - Date-range filter
6. Re-auth/2FA challenges surface clearly in the UI (or via a notification) instead of silently failing.

## Non-goals (explicit)
- Not a multi-user SaaS. No login system on the local UI in v1 (assume localhost-only).
- Not a feed/DM/profile clone — saved-posts archive only.
- No mobile app, no PWA polish in v1.
- No re-uploading, re-sharing, or publishing back to Instagram.
- No scraping of accounts other than the operator's own saved set.
- No ML/tagging/auto-categorization in v1.

## Constraints & risks
- **Account-flag risk:** any persistent automated session against Instagram risks rate-limits, checkpoints, or bans. Mitigations: low poll cadence, jitter, mobile-app-mimicking client, persistent device-id, recommend running against a non-primary account. Phase 1 must compare libraries on this axis.
- **Auth durability:** decision deferred to Phase 1 research (instagrapi persistent session vs. cookie-extraction vs. instaloader). Whichever is chosen must support unattended re-use of a saved session and graceful 2FA challenge surfacing.
- **ToS:** Instagram's ToS technically prohibits automated access. Personal-archive use is widely tolerated but not endorsed by Meta. User accepts this risk.
- **Media storage:** local disk grows unbounded. Plan must include a content-addressed media store (sha256-named files) and a configurable retention/dedup story.
- **Collections coverage:** confirm in Phase 1 that the chosen library exposes named collections and their post lists (not only the global "All Posts" feed). If a single post can appear in multiple collections via the API, model that natively. If the library only exposes "All Posts," fall back to that and document the gap.

## Stack (decided)
- Backend: Python 3.11+, FastAPI, SQLite (single-file DB, easy to back up)
- Frontend: separate React app (Vite + TS recommended; finalize in Phase 2)
- Media: downloaded to local disk under content-addressed paths
- Deployment: local Mac via launchd plist or docker-compose; Phase 2 picks one
- Background sync: APScheduler or a simple asyncio loop; Phase 2 decides

## Open questions deferred to Phase 1
1. Which Instagram client library best balances reliability, collection support, and ban risk for unattended long-running use?
2. Cookie/session refresh strategy and 2FA challenge flow — what does the re-auth UX look like?
3. Media schema: store originals only, or originals + thumbnails?
4. Should the local web UI have any auth at all, or strictly localhost-bind?

## Decisions captured this phase
- Single-account, single-user, self-hosted
- Python backend + separate React frontend
- Always-on server on local Mac, launchd or docker (decision deferred to plan phase)
- Media downloaded locally
- Brand-new repo at `/Users/genehan/projects/claudehome-projects/instagram-logger`
- UI = "All Posts" grid + per-collection grids + post detail modal showing which collections each post is in (Instagram-faithful many-to-many)
- Retention = keep forever, flag unsaved/deleted
- Auth backend = defer to Phase 1 research
