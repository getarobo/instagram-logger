"""E6 — GET /api/ingest/status tests.

3 tests:
- test_status_returns_counts: exact aggregate counts across post states + media states.
- test_status_returns_alert_timestamps: pre-set ingest_meta timestamps appear in response.
- test_status_no_secret_required: endpoint returns 200 without X-Ingest-Secret header.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_data_dir: Path):
    """TestClient with migrations applied; no ingest_secret needed for /api/ingest/status."""
    from backend.main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _seed_author(conn, now: str = "2026-01-01T00:00:00+00:00") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO authors(id, username, full_name, is_private, "
        "profile_pic_url, first_seen_at, last_seen_at) "
        "VALUES ('unknown', 'unknown', NULL, 0, NULL, ?, ?)",
        (now, now),
    )


def _insert_post(
    conn,
    post_id: str,
    shortcode: str,
    state: str,
    now: str = "2026-01-01T00:00:00+00:00",
) -> None:
    conn.execute(
        "INSERT INTO posts(id, shortcode, author_id, author_username_denorm, "
        "caption, media_kind, taken_at, saved_at, first_seen_at, "
        "last_seen_in_saved_at, is_unsaved, is_source_deleted, state, retry_count) "
        "VALUES (?, ?, 'unknown', 'unknown', NULL, 'unknown', NULL, NULL, ?, ?, 0, 0, ?, 0)",
        (post_id, shortcode, now, now, state),
    )


def _insert_media_sentinel(conn) -> None:
    """Ensure the '' sentinel row exists so post_media FKs resolve."""
    conn.execute(
        "INSERT OR IGNORE INTO media_files(sha256, file_path, mime_type, "
        "file_size_bytes, fetched_at) VALUES ('', '', NULL, 0, '')"
    )


def _insert_post_media(
    conn,
    post_id: str,
    carousel_index: int,
    state: str,
) -> None:
    _insert_media_sentinel(conn)
    conn.execute(
        "INSERT INTO post_media(post_id, media_sha256, carousel_index, media_type, state) "
        "VALUES (?, '', ?, 'image', ?)",
        (post_id, carousel_index, state),
    )


# ---------------------------------------------------------------------------
# test_status_returns_counts
# ---------------------------------------------------------------------------


def test_status_returns_counts(client: TestClient, tmp_data_dir: Path) -> None:
    """Insert posts and media in various states; assert counts match exactly."""
    from backend.db.connection import get_connection

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"
    _seed_author(conn, now)

    # 2 enriched, 1 lost, 3 placeholder
    _insert_post(conn, "e1", "sc_e1", "enriched", now)
    _insert_post(conn, "e2", "sc_e2", "enriched", now)
    _insert_post(conn, "l1", "sc_l1", "lost", now)
    _insert_post(conn, "ph1", "sc_ph1", "placeholder", now)
    _insert_post(conn, "ph2", "sc_ph2", "placeholder", now)
    _insert_post(conn, "ph3", "sc_ph3", "placeholder", now)
    conn.commit()

    # 2 present media, 1 media_failed, 1 pending (pending not counted in either bucket)
    _insert_post_media(conn, "e1", 0, "present")
    _insert_post_media(conn, "e2", 0, "present")
    _insert_post_media(conn, "l1", 0, "media_failed")
    _insert_post_media(conn, "ph1", 0, "pending")
    conn.commit()

    r = client.get("/api/ingest/status")
    assert r.status_code == 200
    body = r.json()

    assert body["total_discovered"] == 6
    assert body["total_enriched"] == 2
    assert body["total_lost"] == 1
    assert body["total_placeholder"] == 3
    assert body["total_media_present"] == 2
    assert body["total_media_failed"] == 1


# ---------------------------------------------------------------------------
# test_status_returns_alert_timestamps
# ---------------------------------------------------------------------------


def test_status_returns_alert_timestamps(client: TestClient, tmp_data_dir: Path) -> None:
    """Pre-set ingest_meta timestamps via direct UPDATE; assert they appear in response."""
    from backend.db.connection import get_connection

    conn = get_connection()
    logged_out_ts = "2026-01-02T10:00:00+00:00"
    throttling_ts = "2026-01-02T11:00:00+00:00"
    conn.execute(
        "UPDATE ingest_meta SET last_logged_out_at = ?, last_throttling_at = ? WHERE id = 1",
        (logged_out_ts, throttling_ts),
    )
    conn.commit()

    r = client.get("/api/ingest/status")
    assert r.status_code == 200
    body = r.json()

    assert body["last_logged_out_at"] == logged_out_ts
    assert body["last_throttling_at"] == throttling_ts


# ---------------------------------------------------------------------------
# test_status_no_secret_required
# ---------------------------------------------------------------------------


def test_status_no_secret_required(client: TestClient) -> None:
    """GET /api/ingest/status without X-Ingest-Secret header → 200 (not secret-gated)."""
    r = client.get("/api/ingest/status")
    assert r.status_code == 200
    body = r.json()
    # Basic shape check
    assert "phase" in body
    assert "total_discovered" in body
    assert "total_enriched" in body
    assert "total_media_present" in body
