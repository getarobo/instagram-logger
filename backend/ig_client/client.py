"""instagrapi wrapper. Knows how to load/save settings and issue calls.

Follows the canonical instagrapi best-practice login flow
(https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html):

  1. Construct `Client()`.
  2. Set built-in inter-API-call jitter (`cl.delay_range`).
  3. If a previous `dump_settings()` exists, `load_settings()` to restore
     the same device UUIDs / cookies / user-agent IG already trusts.
  4. Otherwise (first-ever login only), pin device locale / country /
     country_code / timezone_offset from config so the very first login
     presents a stable, plausible identity instead of randomized
     instagrapi defaults that change every run.
  5. After a successful login (or sessionid import), `dump_settings()`
     so the next run reuses the same identity.

Steps 3 vs 4 are mutually exclusive: persisted settings are authoritative
once they exist — re-pinning device fields mid-life would change the
fingerprint of an already-trusted session, which IG treats as suspicious.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.ig_client.session import with_session_retry


class IgClient:
    """Thin wrapper around `instagrapi.Client`.

    Loaded lazily so importing this module does not fail in environments
    where instagrapi is not installed (CI, contract tests).
    """

    def __init__(self, settings_file: Path | None = None) -> None:
        self.settings_file = settings_file or settings.settings_file
        self._cl: Any | None = None

    # ---- session lifecycle ----------------------------------------------

    @property
    def cl(self) -> Any:
        if self._cl is None:
            from instagrapi import Client  # type: ignore[import-untyped]

            cl = Client()
            self._configure_runtime(cl)
            if self.settings_file.exists():
                cl.load_settings(str(self.settings_file))
            else:
                self._apply_device_pin(cl)
            self._cl = cl
        return self._cl

    def _configure_runtime(self, cl: Any) -> None:
        """Settings safe to apply on every run — no fingerprint impact."""
        cl.delay_range = [
            settings.ig_client_delay_min,
            settings.ig_client_delay_max,
        ]

    def _apply_device_pin(self, cl: Any) -> None:
        """First-login-only device fingerprint pinning.

        After the first login, `dump_settings()` persists this exact
        identity and `load_settings()` replays it on every subsequent
        run. Mutating these fields after that point would change the
        fingerprint of a trusted session — exactly what we don't want.
        """
        if settings.ig_device_country:
            cl.set_country(settings.ig_device_country)
        if settings.ig_device_country_code is not None:
            cl.set_country_code(settings.ig_device_country_code)
        if settings.ig_device_locale:
            cl.set_locale(settings.ig_device_locale)
        if settings.ig_device_timezone_offset is not None:
            cl.set_timezone_offset(settings.ig_device_timezone_offset)

    def has_settings(self) -> bool:
        return self.settings_file.exists()

    def save_settings(self) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.cl.dump_settings(str(self.settings_file))

    def login(
        self,
        username: str,
        password: str,
        verification_code: str | None = None,
    ) -> None:
        if verification_code:
            self.cl.login(username, password, verification_code=verification_code)
        else:
            self.cl.login(username, password)
        self.save_settings()

    def login_by_sessionid(self, sessionid: str) -> None:
        """Bypass password login by importing a `sessionid` copied from a
        working IG web session (Chrome DevTools → Application → Cookies →
        instagram.com → sessionid).

        Used when the device is flagged for password-based logins but a
        browser session is alive. Validates the imported session with a
        cheap `get_timeline_feed()` auth probe before persisting.
        """
        self.cl.login_by_sessionid(sessionid)
        self.cl.get_timeline_feed()
        self.save_settings()

    def relogin(self) -> None:
        self.cl.relogin()

    # ---- call surface used by ingest ------------------------------------

    def collection_medias_by_name(self, name: str, amount: int = 20) -> list[Any]:
        """Plan §9: first-page-ish fetch from "All Posts"."""
        retry = with_session_retry(relogin=self.relogin)

        @retry
        def _call() -> list[Any]:
            return list(self.cl.collection_medias_by_name(name, amount=amount))

        return _call()

    def media_to_dict(self, media: Any) -> dict[str, Any]:
        """Best-effort serialization of an instagrapi `Media` to JSON."""
        try:
            return json.loads(media.model_dump_json())
        except Exception:
            try:
                return media.dict()
            except Exception:
                return {"_repr": repr(media)}
