"""Group E — Endpoint contracts & secret gating tests."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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
# test_state_requires_secret
# ---------------------------------------------------------------------------


def test_state_requires_secret(client: TestClient) -> None:
    """GET /api/ingest/extension/state: no header → 401, wrong → 401, correct → 200."""
    r = client.get("/api/ingest/extension/state")
    assert r.status_code == 401

    r = client.get("/api/ingest/extension/state", headers={"X-Ingest-Secret": "wrong"})
    assert r.status_code == 401

    r = client.get("/api/ingest/extension/state", headers=HEADERS)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# test_state_returns_shape
# ---------------------------------------------------------------------------


def test_state_returns_shape(client: TestClient) -> None:
    """State response has all required top-level fields."""
    r = client.get("/api/ingest/extension/state", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    for field in (
        "phase_suggestion",
        "total_discovered",
        "total_enriched",
        "total_lost",
        "total_placeholder",
        "collections_known",
    ):
        assert field in body, f"Missing field: {field}"
    assert isinstance(body["collections_known"], list)


# ---------------------------------------------------------------------------
# test_shortcodes_inserts_placeholders
# ---------------------------------------------------------------------------


def test_shortcodes_inserts_placeholders(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /shortcodes inserts placeholder row with correct state + recency_rank."""
    payload = {
        "source": "all_posts",
        "items": [
            {"shortcode": "abc123", "recency_rank": 0, "thumb_url": "http://example/x.jpg"}
        ],
    }
    r = client.post("/api/ingest/extension/shortcodes", json=payload, headers=HEADERS)
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT state, recency_rank FROM posts WHERE shortcode = 'abc123'"
    ).fetchone()
    assert row is not None
    assert row["state"] == "placeholder"
    assert row["recency_rank"] == 0


# ---------------------------------------------------------------------------
# test_post_outcome_lost
# ---------------------------------------------------------------------------


def test_post_outcome_lost(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /post with outcome=lost sets state='lost' and next_retry_at ~7 days out."""
    # First create a placeholder
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "abc", "recency_rank": 0}]},
        headers=HEADERS,
    )

    r = client.post(
        "/api/ingest/extension/post",
        json={"shortcode": "abc", "outcome": "lost"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT state, next_retry_at FROM posts WHERE shortcode = 'abc'"
    ).fetchone()
    assert row["state"] == "lost"
    assert row["next_retry_at"] is not None
    # Sanity: next_retry_at should be roughly now+7d
    retry_dt = datetime.fromisoformat(row["next_retry_at"])
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    delta = retry_dt - datetime.now(UTC)
    assert timedelta(days=6) < delta < timedelta(days=8)


# ---------------------------------------------------------------------------
# test_post_outcome_enriched
# ---------------------------------------------------------------------------


def test_post_outcome_enriched(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /post with outcome=enriched: state='enriched', payload_fetched_at set, slides inserted."""
    # Seed placeholder
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "xyz", "recency_rank": 1}]},
        headers=HEADERS,
    )

    payload = {
        "shortcode": "xyz",
        "outcome": "enriched",
        "author": {"id": "auth1", "username": "testuser", "is_private": False},
        "caption": "A caption",
        "taken_at": "2026-01-01T12:00:00+00:00",
        "media_kind": "carousel",
        "slides": [
            {"carousel_index": 0, "media_url": "http://cdn/img0.jpg", "media_type": "image"},
            {"carousel_index": 1, "media_url": "http://cdn/img1.jpg", "media_type": "image"},
        ],
    }
    r = client.post("/api/ingest/extension/post", json=payload, headers=HEADERS)
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT state, payload_fetched_at FROM posts WHERE shortcode = 'xyz'"
    ).fetchone()
    assert row["state"] == "enriched"
    assert row["payload_fetched_at"] is not None

    media_rows = conn.execute(
        "SELECT carousel_index, state FROM post_media WHERE post_id = "
        "(SELECT id FROM posts WHERE shortcode = 'xyz') ORDER BY carousel_index"
    ).fetchall()
    assert len(media_rows) == 2
    assert all(r["state"] == "pending" for r in media_rows)


# ---------------------------------------------------------------------------
# test_media_exists_head
# ---------------------------------------------------------------------------


def test_media_exists_head(client: TestClient, tmp_data_dir: Path) -> None:
    """HEAD /media/exists: 204 for known sha, 404 for unknown."""
    from backend.db.connection import get_connection

    conn = get_connection()
    known_sha = "a" * 64
    conn.execute(
        "INSERT INTO media_files(sha256, file_path, mime_type, file_size_bytes, "
        "fetched_at) VALUES (?, 'media/aa/file.jpg', 'image/jpeg', 100, '2026-01-01')",
        (known_sha,),
    )

    r = client.head(
        f"/api/ingest/extension/media/exists?sha={known_sha}", headers=HEADERS
    )
    assert r.status_code == 204

    r = client.head(
        "/api/ingest/extension/media/exists?sha=" + "b" * 64, headers=HEADERS
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# test_media_upload_sha_verify
# ---------------------------------------------------------------------------


def test_media_upload_sha_verify(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /media with correct sha → 200, file persisted, slide state='present'.
    POST with wrong sha → 400."""
    # Seed a post + slide via shortcodes + post endpoint
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "media_post", "recency_rank": 0}]},
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/post",
        json={
            "shortcode": "media_post",
            "outcome": "enriched",
            "author": {"id": "aut99", "username": "u99"},
            "slides": [{"carousel_index": 0, "media_url": "http://x/y.jpg", "media_type": "image"}],
        },
        headers=HEADERS,
    )

    from backend.db.connection import get_connection

    conn = get_connection()
    post_row = conn.execute(
        "SELECT id FROM posts WHERE shortcode = 'media_post'"
    ).fetchone()
    post_id = post_row["id"]

    data = b"image bytes here"
    correct_sha = hashlib.sha256(data).hexdigest()

    r = client.post(
        "/api/ingest/extension/media",
        data={"sha256": correct_sha, "mime": "image/jpeg", "post_id": post_id, "slide_idx": 0},
        files={"file": ("test.jpg", io.BytesIO(data), "image/jpeg")},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text

    # Slide state should flip to 'present'
    slide = conn.execute(
        "SELECT state FROM post_media WHERE post_id = ? AND carousel_index = 0", (post_id,)
    ).fetchone()
    assert slide["state"] == "present"

    # Wrong sha → 400
    r = client.post(
        "/api/ingest/extension/media",
        data={"sha256": "z" * 64, "mime": "image/jpeg", "post_id": post_id, "slide_idx": 0},
        files={"file": ("test2.jpg", io.BytesIO(b"other data"), "image/jpeg")},
        headers=HEADERS,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# test_heartbeat_logged_out_calls_alert
# ---------------------------------------------------------------------------


def test_heartbeat_logged_out_calls_alert(client: TestClient, tmp_data_dir: Path) -> None:
    """Heartbeat with state='logged_out' fires alert once; rate-limited on second call."""
    from backend.db.connection import get_connection

    mock_alert = MagicMock()
    with patch("backend.notify.telegram.alert", mock_alert):
        r = client.post(
            "/api/ingest/extension/heartbeat",
            json={"state": "logged_out"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert mock_alert.call_count == 1

        # Second call immediately (within 30-min window) → rate-limited → no second alert
        r = client.post(
            "/api/ingest/extension/heartbeat",
            json={"state": "logged_out"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert mock_alert.call_count == 1  # still 1, not 2

    # Simulate window expiry: set last_alert_at to 31 minutes ago
    conn = get_connection()
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat(timespec="seconds")
    conn.execute("UPDATE ingest_meta SET last_alert_at = ? WHERE id = 1", (past,))

    mock_alert2 = MagicMock()
    with patch("backend.notify.telegram.alert", mock_alert2):
        r = client.post(
            "/api/ingest/extension/heartbeat",
            json={"state": "logged_out"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert mock_alert2.call_count == 1  # fires again after window expires


# ---------------------------------------------------------------------------
# test_retry_page_resets_state
# ---------------------------------------------------------------------------


def test_retry_page_resets_state(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /posts/{id}/retry-page resets lost post to placeholder and sets priority target."""
    # Create a post and mark it lost
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "retry_sc", "recency_rank": 0}]},
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/post",
        json={"shortcode": "retry_sc", "outcome": "lost"},
        headers=HEADERS,
    )

    from backend.db.connection import get_connection

    conn = get_connection()
    post_row = conn.execute(
        "SELECT id, state FROM posts WHERE shortcode = 'retry_sc'"
    ).fetchone()
    assert post_row["state"] == "lost"
    post_id = post_row["id"]

    r = client.post(f"/api/posts/{post_id}/retry-page")
    assert r.status_code == 200

    row = conn.execute(
        "SELECT state, retry_count FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert row["state"] == "placeholder"
    assert row["retry_count"] == 0

    meta = conn.execute(
        "SELECT priority_target_post_id FROM ingest_meta WHERE id = 1"
    ).fetchone()
    assert meta["priority_target_post_id"] == post_id


# ---------------------------------------------------------------------------
# test_retry_media_resets_slide
# ---------------------------------------------------------------------------


def test_retry_media_resets_slide(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /posts/{id}/retry-media/0 resets media_failed slide to pending."""
    # Create enriched post with one slide
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "slide_retry", "recency_rank": 0}]},
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/post",
        json={
            "shortcode": "slide_retry",
            "outcome": "enriched",
            "author": {"id": "aut77", "username": "u77"},
            "slides": [{"carousel_index": 0, "media_url": "http://x/z.jpg", "media_type": "image"}],
        },
        headers=HEADERS,
    )

    from backend.db.connection import get_connection

    conn = get_connection()
    post_row = conn.execute(
        "SELECT id FROM posts WHERE shortcode = 'slide_retry'"
    ).fetchone()
    post_id = post_row["id"]

    # Manually set slide to media_failed
    conn.execute(
        "UPDATE post_media SET state = 'media_failed', retry_count = 3 "
        "WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    )

    r = client.post(f"/api/posts/{post_id}/retry-media/0")
    assert r.status_code == 200

    slide = conn.execute(
        "SELECT state, retry_count FROM post_media WHERE post_id = ? AND carousel_index = 0",
        (post_id,),
    ).fetchone()
    assert slide["state"] == "pending"
    assert slide["retry_count"] == 0


# ---------------------------------------------------------------------------
# test_heartbeat_precedence_does_not_downgrade
# ---------------------------------------------------------------------------


def test_heartbeat_precedence_does_not_downgrade(client: TestClient, tmp_data_dir: Path) -> None:
    """Higher-precedence phase is never downgraded by a lower-precedence heartbeat."""
    # Step 1: logged_out heartbeat sets last_phase = 'logged_out' (prec 5)
    from unittest.mock import MagicMock, patch

    with patch("backend.notify.telegram.alert", MagicMock()):
        r = client.post(
            "/api/ingest/extension/heartbeat",
            json={"state": "logged_out"},
            headers=HEADERS,
        )
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    meta = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert meta["last_phase"] == "logged_out"

    # Step 2: throttling_suspected heartbeat (prec 3) must NOT downgrade logged_out (prec 5)
    with patch("backend.notify.telegram.alert", MagicMock()):
        r = client.post(
            "/api/ingest/extension/heartbeat",
            json={"state": "throttling_suspected"},
            headers=HEADERS,
        )
    assert r.status_code == 200

    meta = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert meta["last_phase"] == "logged_out", (
        "throttling_suspected must not downgrade logged_out"
    )

    # Step 3: active phase (prec 1) must NOT downgrade logged_out (prec 5)
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "ok", "phase": "enrichment"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    meta = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert meta["last_phase"] == "logged_out", (
        "active phase enrichment must not downgrade logged_out"
    )


# ---------------------------------------------------------------------------
# test_upload_too_large_returns_413
# ---------------------------------------------------------------------------


def test_upload_too_large_returns_413(client: TestClient, tmp_data_dir: Path) -> None:
    """Upload exceeding the size cap returns 413 and leaves no file on disk."""
    import backend.media.from_upload as from_upload_mod

    # Seed a post + slide so the endpoint can be reached
    client.post(
        "/api/ingest/extension/shortcodes",
        json={"source": "all_posts", "items": [{"shortcode": "big_upload", "recency_rank": 0}]},
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/post",
        json={
            "shortcode": "big_upload",
            "outcome": "enriched",
            "author": {"id": "aut_big", "username": "biguser"},
            "slides": [{"carousel_index": 0, "media_url": "http://x/big.jpg", "media_type": "image"}],
        },
        headers=HEADERS,
    )

    from backend.db.connection import get_connection

    conn = get_connection()
    post_row = conn.execute("SELECT id FROM posts WHERE shortcode = 'big_upload'").fetchone()
    post_id = post_row["id"]

    # Monkey-patch the cap to 1024 bytes so the test is fast
    original_cap = from_upload_mod._MAX_UPLOAD_BYTES
    from_upload_mod._MAX_UPLOAD_BYTES = 1024
    try:
        data = b"a" * 2048  # exceeds the patched cap
        sha = __import__("hashlib").sha256(data).hexdigest()

        r = client.post(
            "/api/ingest/extension/media",
            data={"sha256": sha, "mime": "image/jpeg", "post_id": post_id, "slide_idx": 0},
            files={"file": ("big.jpg", io.BytesIO(data), "image/jpeg")},
            headers=HEADERS,
        )
    finally:
        from_upload_mod._MAX_UPLOAD_BYTES = original_cap

    assert r.status_code == 413, f"Expected 413, got {r.status_code}: {r.text}"

    # No file should have been persisted under the media directory
    media_dir = tmp_data_dir / "media"
    persisted = list(media_dir.rglob("*.jpg")) if media_dir.exists() else []
    assert persisted == [], f"Unexpected files on disk: {persisted}"
