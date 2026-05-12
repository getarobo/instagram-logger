# instagram-logger extension

Chrome MV3 extension for the saved-posts archiver. Talks to backend at 127.0.0.1:8000.

## Build

```bash
cd extension
pnpm install
pnpm build
```

## Load in Chrome

1. Open `chrome://extensions`
2. Enable "Developer mode" (top-right toggle)
3. Click "Load unpacked"
4. Select `extension/dist`

## First-run setup

1. Click the extension icon in the Chrome toolbar
2. Enter your `INGEST_SECRET` value (from backend `.env` — `INGEST_SECRET=...`)
3. Click **Save**
4. Click **Refresh** — backend status should show your archive state

## Secret rotation

If you change `INGEST_SECRET` in backend `.env` and restart the backend:
1. The extension will start getting 401 errors (visible in popup as "Error: …401")
2. Open the popup, enter the new secret, click Save
3. Click Refresh to confirm round-trip succeeds

## E2 scope notes

E2 is **skeleton only**. Content scripts are placeholder stubs that log to the console. No scraping yet — that is E3 (discovery) and E4 (enrichment + media).

The popup does provide a working:
- Secret entry and save
- Backend `/api/ingest/extension/state` round-trip (Refresh button)
- Local phase Pause / Resume toggle (picked up by the background service worker on next alarm tick)

## E3 fixes — manual smoke checklist

After the E3 critical fixes land, verify the following manually before declaring E3 complete:

1. Open the popup. Confirm the **Instagram username** field appears above the secret entry.
2. Leave the username blank — the **Start Discovery** button must be **disabled** (greyed out, tooltip visible).
3. Enter your IG username (e.g. `your_ig_username`) and click **Save**. The button becomes enabled.
4. Click **Start Discovery**. Confirm the phase transitions to `discovery_all` in the popup status area and a new tab opens at `https://www.instagram.com/<username>/saved/all-posts/`.
5. **SW eviction test** — during `discovery_collections` phase (Pass B iterating collections):
   a. Open `chrome://extensions`, find instagram-logger, click **Service worker** link to open DevTools.
   b. In DevTools → Application → Service Workers, click **Stop** to simulate SW eviction.
   c. Navigate back to the extension popup; click **Refresh**.
   d. Confirm `chrome.storage.local` key `pending_collections` still contains the remaining collections (visible in Application → Storage → Local Storage for the extension origin).
   e. Reload the stopped service worker (Chrome auto-restarts it on the next message). Discovery should resume from the persisted queue without restarting from the beginning.
6. Prod build check: `pnpm build` → inspect `dist/manifest.json` — `host_permissions` must NOT contain `localhost` or `127.0.0.1:9090`.
7. Dev build check: `EXT_DEV=1 pnpm build` → inspect `dist/manifest.json` — `host_permissions` MUST include `http://localhost:9090/*` and `http://127.0.0.1:9090/*`.

## Development

```bash
pnpm dev    # Vite dev server with HMR (for popup iteration)
pnpm tsc    # Type-check only (no emit)
pnpm build  # Production build → dist/
EXT_DEV=1 pnpm build  # Dev build with localhost permissions for fake-IG testing
```
