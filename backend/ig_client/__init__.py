"""Instagram client wrapper layer (instagrapi + session retry).

`get_client()` returns either the real instagrapi-backed client or a fake
in-process fixture, controlled by the `IG_CLIENT` env var (see
`backend.config.Settings.ig_client`).
"""

from __future__ import annotations

from typing import Any

from backend.config import settings


def get_client() -> Any:
    """Return the configured IG client.

    - `IG_CLIENT=fake` -> `FakeIgClient` (offline; no auth required).
    - anything else    -> real `IgClient` (instagrapi-backed).
    """
    mode = (settings.ig_client or "real").lower()
    if mode == "fake":
        from backend.ig_client.fake import FakeIgClient

        return FakeIgClient()
    from backend.ig_client.client import IgClient

    return IgClient()


__all__ = ["get_client"]
