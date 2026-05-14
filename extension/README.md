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

### 1. Generate an `INGEST_SECRET`

The extension and backend authenticate via a shared secret you create yourself.
Any random ≥16-char string works. Easy ways to generate one:

```bash
# macOS / Linux — 32 random bytes, base64-encoded:
openssl rand -base64 32

# Or via Python:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Or via Node:
node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"
```

Pick one; copy the output to your clipboard.

### 2. Set it in the backend

From the repo root:

```bash
# If .env doesn't exist yet, copy the template:
cp .env.example .env

# Open .env in your editor and replace the placeholder
#   INGEST_SECRET=replace-me-with-a-random-token
# with your generated token:
#   INGEST_SECRET=<paste-here>
```

Then start (or restart) the backend so it picks up the new value:

```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 3. Paste it into the extension

1. Click the extension icon in the Chrome toolbar.
2. Enter your **Instagram username** (e.g. `genehan`).
3. Paste the same `INGEST_SECRET` you generated above.
4. Click **Save**.
5. Click **Refresh** — backend status should show your archive state.

The extension stores the secret in `chrome.storage.local`, scoped to its own
extension ID. It never leaves the loopback network surface.

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
