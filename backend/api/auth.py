"""GET /api/auth/status — surfaces the auth state machine to the frontend.

States returned:
  - LOGGED_IN          : settings file present and last sync ran cleanly
  - SESSION_EXPIRED    : settings file present but last run hit LoginRequired
                         / PleaseWaitFewMinutes (state='auth_required'). The
                         user needs to re-run `just import-session` (or
                         `just login` if they have a working password flow).
  - NEEDS_FIRST_LOGIN  : no settings file yet
  - CHALLENGE_PENDING  : reserved for B5; not emitted yet

When the fake IG client is selected (`IG_CLIENT=fake`), we always report
LOGGED_IN so the offline `just sync-fake` flow can render posts without
operator intervention.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.config import settings
from backend.db import repo
from backend.db.connection import get_connection

router = APIRouter()


@router.get("/auth/status")
def auth_status() -> dict:
    if settings.ig_client.lower() == "fake":
        return {"state": "LOGGED_IN", "challenge_kind": None, "last_error": None}

    if not settings.settings_file.exists():
        return {
            "state": "NEEDS_FIRST_LOGIN",
            "challenge_kind": None,
            "last_error": None,
        }

    # Settings exist — check whether the last scheduled run could still
    # talk to IG. `auth_required` is set by reconcile.py when instagrapi
    # raises ChallengeRequired / LoginRequired / PleaseWaitFewMinutes.
    conn = get_connection()
    last = repo.latest_sync_run(conn)
    if last is not None and last.get("state") == "auth_required":
        return {
            "state": "SESSION_EXPIRED",
            "challenge_kind": None,
            "last_error": last.get("errors_json"),
        }

    return {"state": "LOGGED_IN", "challenge_kind": None, "last_error": None}
