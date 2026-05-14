"""GET /api/ingest/status — loopback-only frontend-facing status endpoint.

Not secret-gated (unlike /api/ingest/extension/state which requires
X-Ingest-Secret). Loopback enforcement is handled by config.assert_bind_allowed
at startup (127.0.0.1 only).

Plan §6 / E6 deliverable.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter

from backend.db.connection import get_connection

router = APIRouter()


def _get_ingest_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Derive ingest status from ingest_meta + posts + post_media."""
    # ingest_meta fields (defensive: row may not exist yet)
    phase: str | None = None
    last_heartbeat_at: str | None = None
    last_logged_out_at: str | None = None
    last_throttling_at: str | None = None
    last_storage_low_at: str | None = None
    last_alert_at: str | None = None

    meta_row = conn.execute(
        """
        SELECT last_phase, last_heartbeat_at, last_logged_out_at,
               last_throttling_at, last_storage_low_at, last_alert_at
        FROM ingest_meta WHERE id = 1
        """
    ).fetchone()
    if meta_row is not None:
        phase = meta_row["last_phase"]
        last_heartbeat_at = meta_row["last_heartbeat_at"]
        last_logged_out_at = meta_row["last_logged_out_at"]
        last_throttling_at = meta_row["last_throttling_at"]
        last_storage_low_at = meta_row["last_storage_low_at"]
        last_alert_at = meta_row["last_alert_at"]

    # Post state counts — SQLite doesn't support FILTER; use SUM+CASE
    counts_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN state = 'enriched'    THEN 1 ELSE 0 END) AS enriched,
            SUM(CASE WHEN state = 'lost'         THEN 1 ELSE 0 END) AS lost,
            SUM(CASE WHEN state = 'placeholder'  THEN 1 ELSE 0 END) AS placeholder,
            COUNT(*) AS total
        FROM posts
        """
    ).fetchone()

    total_discovered = int(counts_row["total"] or 0)
    total_enriched = int(counts_row["enriched"] or 0)
    total_lost = int(counts_row["lost"] or 0)
    total_placeholder = int(counts_row["placeholder"] or 0)

    # Media state counts
    media_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN state = 'present'      THEN 1 ELSE 0 END) AS media_present,
            SUM(CASE WHEN state = 'media_failed' THEN 1 ELSE 0 END) AS media_failed
        FROM post_media
        """
    ).fetchone()

    total_media_present = int(media_row["media_present"] or 0)
    total_media_failed = int(media_row["media_failed"] or 0)

    return {
        "phase": phase,
        "last_heartbeat_at": last_heartbeat_at,
        "last_logged_out_at": last_logged_out_at,
        "last_throttling_at": last_throttling_at,
        "last_storage_low_at": last_storage_low_at,
        "last_alert_at": last_alert_at,
        "total_discovered": total_discovered,
        "total_enriched": total_enriched,
        "total_lost": total_lost,
        "total_placeholder": total_placeholder,
        "total_media_present": total_media_present,
        "total_media_failed": total_media_failed,
    }


@router.get("/ingest/status")
def get_ingest_status() -> dict[str, Any]:
    """Frontend-facing ingest status (loopback-only, no secret required)."""
    conn = get_connection()
    return _get_ingest_status(conn)
