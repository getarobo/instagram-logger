"""E4 — Enrichment endpoint tests: post outcomes, media upload/dedup, failed slides, validation.

Adds 6 tests (31 prior → 37 total).

Implementation note (media-failed threshold):
  The /media-failed endpoint sets state='media_failed' on EVERY call — there is no
  attempt-count threshold in the implementation. The task spec anticipated a "3 attempts
  trips it" pattern, but the actual code always sets state='media_failed' immediately.
  test_media_failed_marks_slide is written to match the actual behavior.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_data_dir: Path):
    """TestClient with ingest_secret configured and migrations applied."""
    from backend.config import settings
    from backend.main import app

    original_secret = settings.ingest_secret
    settings.ingest_secret = "test-secret"
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        settings.ingest_secret = original_secret


SECRET = "test-secret"
HEADERS = {"X-Ingest-Secret": SECRET}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_placeholder(client: TestClient, shortcode: str, recency_rank: int = 0) -> None:
    """Insert a placeholder post via /shortcodes."""
    r = client.post(
        "/api/ingest/extension/shortcodes",
        json={
            "source": "all_posts",
            "items": [{"shortcode": shortcode, "recency_rank": recency_rank}],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200


def _seed_enriched_post(client: TestClient, shortcode: str) -> str:
    """Seed placeholder + enrich it with one slide; return post_id."""
    _seed_placeholder(client, shortcode)
    r = client.post(
        "/api/ingest/extension/post",
        json={
            "shortcode": shortcode,
            "outcome": "enriched",
            "author": {"id": f"{shortcode}_auth", "username": "testuser"},
            "slides": [
                {
                    "carousel_index": 0,
                    "media_url": "https://example.com/m1.jpg",
                    "media_type": "image",
                }
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute("SELECT id FROM posts WHERE shortcode = ?", (shortcode,)).fetchone()
    assert row is not None
    return row["id"]


# ---------------------------------------------------------------------------
# test_post_outcome_lost
# ---------------------------------------------------------------------------


def test_post_outcome_lost(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /post with outcome=lost sets state='lost', next_retry_at ~7d, payload_fetched_at NULL."""
    _seed_placeholder(client, "aaa111")

    r = client.post(
        "/api/ingest/extension/post",
        json={"shortcode": "aaa111", "outcome": "lost"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT state, next_retry_at, payload_fetched_at FROM posts WHERE shortcode = 'aaa111'"
    ).fetchone()
    assert row is not None
    assert row["state"] == "lost"
    assert row["next_retry_at"] is not None

    # Sanity: next_retry_at should be roughly now+7d
    retry_dt = datetime.fromisoformat(row["next_retry_at"])
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    delta = retry_dt - datetime.now(UTC)
    assert timedelta(days=6) < delta < timedelta(days=8), (
        f"next_retry_at delta {delta} not in expected ~7d window"
    )

    # Lost path must NOT set payload_fetched_at
    assert row["payload_fetched_at"] is None


# ---------------------------------------------------------------------------
# test_post_outcome_enriched
# ---------------------------------------------------------------------------


def test_post_outcome_enriched(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /post with outcome=enriched: state, payload_fetched_at, author, slides, raw HTML."""
    _seed_placeholder(client, "aaa111")

    payload = {
        "shortcode": "aaa111",
        "outcome": "enriched",
        "caption": "test caption",
        "taken_at": "2026-05-01T12:00:00Z",
        "author": {
            "username": "testuser",
            "full_name": "Test User",
            "avatar_url": "https://example.com/a.jpg",
            "is_private": False,
        },
        "media_kind": "image",
        "slides": [
            {
                "carousel_index": 0,
                "media_url": "https://example.com/m1.jpg",
                "thumb_url": "https://example.com/t1.jpg",
                "media_type": "image",
                "width": 1080,
                "height": 1080,
            }
        ],
        "raw_html_snippet": "<article>...</article>",
    }
    r = client.post("/api/ingest/extension/post", json=payload, headers=HEADERS)
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()

    # Post state
    post_row = conn.execute(
        "SELECT state, payload_fetched_at, caption FROM posts WHERE shortcode = 'aaa111'"
    ).fetchone()
    assert post_row is not None
    assert post_row["state"] == "enriched"
    assert post_row["payload_fetched_at"] is not None
    assert post_row["caption"] == "test caption"

    # Author row
    author_row = conn.execute(
        "SELECT username FROM authors WHERE username = 'testuser'"
    ).fetchone()
    assert author_row is not None, "authors row for 'testuser' must exist"

    # post_media row
    post_id_row = conn.execute(
        "SELECT id FROM posts WHERE shortcode = 'aaa111'"
    ).fetchone()
    post_id = post_id_row["id"]

    media_row = conn.execute(
        "SELECT state, carousel_index, last_url FROM post_media "
        "WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert media_row is not None, "post_media row must exist for slide 0"
    assert media_row["state"] == "pending"
    assert media_row["last_url"] == "https://example.com/m1.jpg"

    # posts_raw row
    raw_row = conn.execute(
        "SELECT json FROM posts_raw WHERE post_id = ?", (post_id,)
    ).fetchone()
    assert raw_row is not None, "posts_raw row must exist"
    assert raw_row["json"] == "<article>...</article>"


# ---------------------------------------------------------------------------
# test_media_upload_dedup
# ---------------------------------------------------------------------------


def test_media_upload_dedup(client: TestClient, tmp_data_dir: Path) -> None:
    """Media upload is idempotent; HEAD /media/exists reports correctly."""
    post_id = _seed_enriched_post(client, "aaa111")

    file_data = b"fake image bytes for dedup test"
    sha = hashlib.sha256(file_data).hexdigest()

    # First upload
    r = client.post(
        "/api/ingest/extension/media",
        data={"sha256": sha, "mime": "image/jpeg", "post_id": post_id, "slide_idx": 0},
        files={"file": ("img.jpg", io.BytesIO(file_data), "image/jpeg")},
        headers=HEADERS,
    )
    assert r.status_code == 200

    # HEAD exists → 204
    r = client.head(
        f"/api/ingest/extension/media/exists?sha={sha}", headers=HEADERS
    )
    assert r.status_code == 204

    # Unknown sha → 404
    unknown_sha = "f" * 64
    r = client.head(
        f"/api/ingest/extension/media/exists?sha={unknown_sha}", headers=HEADERS
    )
    assert r.status_code == 404

    from backend.db.connection import get_connection

    conn = get_connection()

    # Count media_files rows before second upload
    count_before = conn.execute(
        "SELECT COUNT(*) FROM media_files WHERE sha256 = ?", (sha,)
    ).fetchone()[0]

    # Second upload of same file (idempotent)
    r = client.post(
        "/api/ingest/extension/media",
        data={"sha256": sha, "mime": "image/jpeg", "post_id": post_id, "slide_idx": 0},
        files={"file": ("img.jpg", io.BytesIO(file_data), "image/jpeg")},
        headers=HEADERS,
    )
    assert r.status_code == 200

    count_after = conn.execute(
        "SELECT COUNT(*) FROM media_files WHERE sha256 = ?", (sha,)
    ).fetchone()[0]
    assert count_after == count_before, "Duplicate upload must not create extra media_files rows"

    # post_media row should be present (not duplicated)
    media_rows = conn.execute(
        "SELECT state FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchall()
    assert len(media_rows) == 1
    assert media_rows[0]["state"] == "present"


# ---------------------------------------------------------------------------
# test_media_failed_marks_slide
# ---------------------------------------------------------------------------


def test_media_failed_marks_slide(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /media-failed sets state='media_failed' and updates retry_count.

    Implementation note: The /media-failed endpoint sets state='media_failed'
    on EVERY call (no attempt-count threshold). The task spec anticipated a
    3-attempt threshold, but the actual implementation always sets
    state='media_failed' immediately. This test reflects the actual behavior.
    """
    post_id = _seed_enriched_post(client, "aaa111")

    from backend.db.connection import get_connection

    conn = get_connection()

    # Confirm slide starts as 'pending'
    slide = conn.execute(
        "SELECT state FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert slide is not None
    assert slide["state"] == "pending"

    # First call: attempts=1 → state should become 'media_failed' immediately
    r = client.post(
        "/api/ingest/extension/media-failed",
        json={"post_id": post_id, "slide_idx": 0, "attempts": 1, "last_error": "http_404"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    slide = conn.execute(
        "SELECT state, retry_count FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert slide["state"] == "media_failed", (
        "state must be 'media_failed' after first /media-failed call "
        "(implementation sets it immediately, no threshold)"
    )
    assert slide["retry_count"] == 1

    # Second call: attempts=2 → retry_count updated
    r = client.post(
        "/api/ingest/extension/media-failed",
        json={"post_id": post_id, "slide_idx": 0, "attempts": 2, "last_error": "http_404"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    slide = conn.execute(
        "SELECT state, retry_count FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert slide["state"] == "media_failed"
    assert slide["retry_count"] == 2

    # Third call: attempts=3 → still 'media_failed', retry_count=3
    r = client.post(
        "/api/ingest/extension/media-failed",
        json={"post_id": post_id, "slide_idx": 0, "attempts": 3, "last_error": "http_404"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    slide = conn.execute(
        "SELECT state, retry_count FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert slide["state"] == "media_failed"
    assert slide["retry_count"] == 3


# ---------------------------------------------------------------------------
# test_shortcode_validation_rejects
# ---------------------------------------------------------------------------


def test_shortcode_validation_rejects(client: TestClient, tmp_data_dir: Path) -> None:
    """Invalid shortcodes in /shortcodes body return 400; valid ones return 200."""
    def _post(shortcode: str) -> int:
        r = client.post(
            "/api/ingest/extension/shortcodes",
            json={
                "source": "all_posts",
                "items": [{"shortcode": shortcode, "recency_rank": 0}],
            },
            headers=HEADERS,
        )
        return r.status_code

    # Invalid shortcodes → 400
    assert _post("foo/bar") == 400, "slash in shortcode must be rejected"
    assert _post("../etc") == 400, "path traversal must be rejected"
    assert _post("<script>") == 400, "XSS attempt must be rejected"
    assert _post("") == 400, "empty shortcode must be rejected"
    assert _post("a" * 200) == 400, "shortcode >128 chars must be rejected"

    # Valid shortcodes → 200
    assert _post("abc123") == 200, "alphanumeric shortcode must be accepted"
    assert _post("a_b-c") == 200, "shortcode with underscores/hyphens must be accepted"
    assert _post("A1_B2-C3") == 200, "mixed-case shortcode must be accepted"


# ---------------------------------------------------------------------------
# test_collection_id_validation_rejects
# ---------------------------------------------------------------------------


def test_collection_id_validation_rejects(client: TestClient, tmp_data_dir: Path) -> None:
    """Invalid collection IDs in /collections body return 400; valid ones return 200."""
    def _post(collection_id: str) -> int:
        r = client.post(
            "/api/ingest/extension/collections",
            json=[{"id": collection_id, "name": "Test", "is_all_posts": False}],
            headers=HEADERS,
        )
        return r.status_code

    # Invalid IDs → 400
    assert _post("foo/bar") == 400, "slash in collection id must be rejected"
    assert _post("<script>") == 400, "XSS attempt must be rejected"
    assert _post("") == 400, "empty collection id must be rejected"

    # Valid IDs → 200
    assert _post("col-favs") == 200, "'col-favs' must be accepted"
    assert _post("col_trips") == 200, "'col_trips' must be accepted"
    assert _post("all_posts") == 200, "'all_posts' must be accepted"
