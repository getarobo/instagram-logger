"""Interactive login CLI: `python -m backend.ig_client.login`.

Prompts for username/password (and 2FA code if challenged), logs in via
instagrapi, then writes `data/instagrapi_settings.json`. Required first
step for the Phase 3 vertical slice (plan §9).
"""

from __future__ import annotations

import getpass
import sys

from backend.config import settings
from backend.ig_client.client import IgClient


def main() -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    print("instagram-logger — interactive login")
    print(f"Settings file will be written to: {settings.settings_file}")
    print()

    username = input("Instagram username: ").strip()
    if not username:
        print("Username is required.", file=sys.stderr)
        return 2
    password = getpass.getpass("Password: ")

    client = IgClient()

    try:
        client.login(username, password)
    except Exception as err:
        # Lazy-import so this module stays importable without instagrapi.
        try:
            from instagrapi.exceptions import ChallengeRequired
        except Exception:
            ChallengeRequired = ()  # type: ignore[assignment]

        if isinstance(err, ChallengeRequired):
            print("Instagram is challenging this login (2FA / SMS / email).")
            code = input("Enter the verification code: ").strip()
            if not code:
                print("No code entered; aborting.", file=sys.stderr)
                return 3
            client.login(username, password, verification_code=code)
        else:
            print(f"Login failed: {err!r}", file=sys.stderr)
            return 1

    client.save_settings()
    print(f"OK. Settings saved to {settings.settings_file}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
