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

## Development

```bash
pnpm dev    # Vite dev server with HMR (for popup iteration)
pnpm tsc    # Type-check only (no emit)
pnpm build  # Production build → dist/
```
