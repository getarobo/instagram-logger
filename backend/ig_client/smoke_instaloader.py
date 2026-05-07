"""One-off smoke test: does instaloader work with our existing Chrome
sessionid via cookie injection (skipping the password login flow)?

Usage:
    .venv/bin/python -m backend.ig_client.smoke_instaloader

Prompts for `sessionid` (required) and optionally `csrftoken` /
`ds_user_id` if the first attempt errors with CSRF/auth complaints.
Tries to fetch ONE saved post and reports outcome with next-step
guidance. Doesn't write any settings files; safe to run repeatedly.

Decision matrix the output triggers:
  * "OK shortcode=..."         -> commit to instaloader-based ingest
  * "needs csrftoken/ds_user_id" -> grab those from DevTools and rerun
  * any other auth error       -> Chrome extension is the path forward
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any


USERNAME = "getarobo"  # update if you swap accounts


def _prompt(label: str, env_var: str, *, required: bool = False) -> str:
    """Read from env first (so this script is reusable in a pipe), else
    `getpass` so the value never lands in shell history.
    """
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    val = getpass.getpass(f"{label} (or set {env_var}): ").strip()
    if required and not val:
        print(f"{label} is required.", file=sys.stderr)
        sys.exit(2)
    return val


def main() -> int:
    try:
        import instaloader  # type: ignore[import-untyped]
    except ImportError:
        print(
            "instaloader is not installed. Run: .venv/bin/pip install instaloader",
            file=sys.stderr,
        )
        return 1

    sid = _prompt("Paste sessionid", "IG_SESSIONID", required=True)
    csrf = _prompt("Optional csrftoken", "IG_CSRFTOKEN")
    ds_user_id = _prompt("Optional ds_user_id", "IG_DS_USER_ID")
    mid = _prompt("Optional mid", "IG_MID")
    ig_did = _prompt("Optional ig_did", "IG_IG_DID")

    cookies: dict[str, str] = {"sessionid": sid}
    if csrf:
        cookies["csrftoken"] = csrf
    if ds_user_id:
        cookies["ds_user_id"] = ds_user_id
    if mid:
        cookies["mid"] = mid
    if ig_did:
        cookies["ig_did"] = ig_did

    print(f"\nProbing with cookies: {sorted(cookies.keys())}")

    L: Any = instaloader.Instaloader()
    L.context._session.cookies.update(cookies)
    # IG's web frontend always sends this app-id header; without it,
    # graphql/query returns 403 even with valid cookies.
    L.context._session.headers.update({
        "X-IG-App-ID": "936619743392459",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
    })
    L.context.username = USERNAME

    try:
        profile = instaloader.Profile.from_username(L.context, USERNAME)
    except Exception as err:
        print(f"\n[FAIL] Profile.from_username errored: {err!r}", file=sys.stderr)
        _hint(err)
        return 1

    print(f"Profile loaded: id={profile.userid} private={profile.is_private}")

    try:
        saved_iter = profile.get_saved_posts()
        first = next(iter(saved_iter), None)
    except Exception as err:
        print(f"\n[FAIL] get_saved_posts errored: {err!r}", file=sys.stderr)
        _hint(err)
        return 1

    if first is None:
        print("\n[OK-ish] Saved feed is empty (or first iteration returned None).")
        print("Auth is working but no posts to fetch — manual sanity check:")
        print(f"  https://www.instagram.com/{USERNAME}/saved/")
        return 0

    caption = (first.caption or "")[:80]
    print(
        f"\n[OK] shortcode={first.shortcode} mediaid={first.mediaid} "
        f"caption={caption!r}"
    )
    print("\ninstaloader can read your saved feed via Chrome sessionid.")
    print("Decision: commit to instaloader-based ingest (Option B).")
    return 0


def _hint(err: Exception) -> None:
    msg = repr(err).lower()
    if "csrf" in msg or "checkpoint" in msg:
        print(
            "Hint: also paste `csrftoken` (and possibly `ds_user_id`) from the "
            "same DevTools cookie panel. Set IG_CSRFTOKEN and rerun.",
            file=sys.stderr,
        )
    elif "401" in msg or "403" in msg or "login" in msg:
        print(
            "Hint: web API also rejecting auth from this machine. The Chrome "
            "extension path is the durable answer at this point.",
            file=sys.stderr,
        )
    elif "404" in msg:
        print(
            f"Hint: profile not found — is USERNAME='{USERNAME}' correct?",
            file=sys.stderr,
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
