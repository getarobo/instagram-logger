"""Import an Instagram session from a working browser session.

Usage:
    python -m backend.ig_client.import_session

Prompts for the `sessionid` cookie value from a logged-in instagram.com
tab (Chrome / Safari / Firefox DevTools → Application/Storage → Cookies
→ https://www.instagram.com → `sessionid` row → "Value" column), feeds
it to `instagrapi`, validates the session with a `get_timeline_feed()`
probe, then writes `data/instagrapi_settings.json`.

This is the recommended path when the password-login flow is failing
because the local device is flagged but a browser session works fine.
The browser already proved the credentials to IG; we just transplant
the resulting session token into instagrapi.
"""

from __future__ import annotations

import getpass
import sys

from backend.config import settings
from backend.ig_client.client import IgClient


def main() -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    print("instagram-logger — session import (no password)")
    print(f"Settings file will be written to: {settings.settings_file}")
    print()
    print("How to get your sessionid:")
    print("  1. Open https://www.instagram.com in Chrome (logged in)")
    print("  2. DevTools (Cmd+Opt+I) → Application → Cookies → instagram.com")
    print("  3. Find the row named `sessionid` and copy its `Value` column")
    print()

    # getpass to avoid leaving the sessionid in shell history / scrollback.
    sessionid = getpass.getpass("Paste sessionid: ").strip()
    if not sessionid:
        print("sessionid is required.", file=sys.stderr)
        return 2

    client = IgClient()
    try:
        client.login_by_sessionid(sessionid)
    except Exception as err:
        print(f"Session import failed: {err!r}", file=sys.stderr)
        print(
            "If the error mentions auth, the sessionid is stale — log out "
            "and back in on instagram.com, then copy a fresh sessionid.",
            file=sys.stderr,
        )
        return 1

    print(f"OK. Settings saved to {settings.settings_file}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
