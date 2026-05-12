"""E3 smoke tests — discovery endpoints: collections, shortcodes (with recency_rank), membership.

Adds 4 tests (27 → 31 total). The 4th test (test_membership_slug_as_id_pattern) exercises
the persistent-queue pattern introduced by E3 Fix 1, where collection IDs are slugs.
"""

from __future__ import annotations

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
# test_collections_upsert
# ---------------------------------------------------------------------------


def test_collections_upsert(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /collections with 2 collections creates DB rows, upsert is idempotent."""
    from backend.db.connection import get_connection

    payload = [
        {"id": "col-favs", "name": "Favorites", "is_all_posts": False},
        {"id": "col-trips", "name": "Trips", "is_all_posts": False},
    ]
    r = client.post("/api/ingest/extension/collections", json=payload, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, is_all_posts FROM collections ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] == "col-favs"
    assert rows[0]["name"] == "Favorites"
    assert rows[0]["is_all_posts"] == 0
    assert rows[1]["id"] == "col-trips"
    assert rows[1]["name"] == "Trips"

    # Upsert: same call again should not fail and should update name
    updated = [
        {"id": "col-favs", "name": "Favorites Updated", "is_all_posts": False},
        {"id": "col-trips", "name": "Trips Updated", "is_all_posts": False},
    ]
    r2 = client.post("/api/ingest/extension/collections", json=updated, headers=HEADERS)
    assert r2.status_code == 200

    rows2 = conn.execute(
        "SELECT id, name FROM collections ORDER BY id"
    ).fetchall()
    assert rows2[0]["name"] == "Favorites Updated"
    assert rows2[1]["name"] == "Trips Updated"


# ---------------------------------------------------------------------------
# test_shortcodes_with_recency_rank
# ---------------------------------------------------------------------------


def test_shortcodes_with_recency_rank(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /shortcodes with 5 items, each with recency_rank, creates placeholder rows
    with the correct recency_rank values stored in the DB."""
    from backend.db.connection import get_connection

    items = [
        {"shortcode": "aaa111", "recency_rank": 0, "thumb_url": "http://fake/aaa111.jpg"},
        {"shortcode": "bbb222", "recency_rank": 1, "thumb_url": "http://fake/bbb222.jpg"},
        {"shortcode": "ccc333", "recency_rank": 2},
        {"shortcode": "ddd444", "recency_rank": 3},
        {"shortcode": "eee555", "recency_rank": 4},
    ]
    payload = {"source": "all_posts", "items": items}
    r = client.post("/api/ingest/extension/shortcodes", json=payload, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 5

    conn = get_connection()
    rows = conn.execute(
        "SELECT shortcode, state, recency_rank FROM posts ORDER BY recency_rank"
    ).fetchall()
    assert len(rows) == 5

    # Verify each shortcode has the correct recency_rank and is in 'placeholder' state
    expected = [
        ("aaa111", 0),
        ("bbb222", 1),
        ("ccc333", 2),
        ("ddd444", 3),
        ("eee555", 4),
    ]
    for row, (shortcode, rank) in zip(rows, expected, strict=True):
        assert row["shortcode"] == shortcode, f"Expected shortcode {shortcode}, got {row['shortcode']}"
        assert row["state"] == "placeholder", f"Expected state=placeholder for {shortcode}"
        assert row["recency_rank"] == rank, f"Expected recency_rank={rank} for {shortcode}, got {row['recency_rank']}"

    # Second POST with same shortcodes must be idempotent (no-op: existing rows not overwritten)
    r2 = client.post("/api/ingest/extension/shortcodes", json=payload, headers=HEADERS)
    assert r2.status_code == 200
    rows2 = conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()
    assert rows2["n"] == 5, "Idempotent insert should not create duplicate rows"


# ---------------------------------------------------------------------------
# test_membership_creates_post_collections_rows
# ---------------------------------------------------------------------------


def test_membership_creates_post_collections_rows(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /membership with shortcode→collection_id pairs creates post_collections rows."""
    from backend.db.connection import get_connection

    # First create collections and shortcodes
    client.post(
        "/api/ingest/extension/collections",
        json=[
            {"id": "col-favs", "name": "Favorites", "is_all_posts": False},
            {"id": "col-trips", "name": "Trips", "is_all_posts": False},
        ],
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/shortcodes",
        json={
            "source": "all_posts",
            "items": [
                {"shortcode": "aaa111", "recency_rank": 0},
                {"shortcode": "bbb222", "recency_rank": 1},
                {"shortcode": "ccc333", "recency_rank": 2},
            ],
        },
        headers=HEADERS,
    )

    # Now POST membership: aaa111 + ccc333 → col-favs, bbb222 → col-trips
    membership = [
        {"shortcode": "aaa111", "collection_id": "col-favs"},
        {"shortcode": "ccc333", "collection_id": "col-favs"},
        {"shortcode": "bbb222", "collection_id": "col-trips"},
    ]
    r = client.post("/api/ingest/extension/membership", json=membership, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["upserted"] == 3

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.shortcode, pc.collection_id
        FROM post_collections pc
        JOIN posts p ON p.id = pc.post_id
        ORDER BY p.shortcode, pc.collection_id
        """
    ).fetchall()
    assert len(rows) == 3

    pairs = [(r["shortcode"], r["collection_id"]) for r in rows]
    assert ("aaa111", "col-favs") in pairs
    assert ("bbb222", "col-trips") in pairs
    assert ("ccc333", "col-favs") in pairs

    # Upsert: posting same membership again should be idempotent
    r2 = client.post("/api/ingest/extension/membership", json=membership, headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["upserted"] == 3
    rows2 = conn.execute("SELECT COUNT(*) AS n FROM post_collections").fetchone()
    assert rows2["n"] == 3, "Idempotent upsert should not create duplicate rows"


# ---------------------------------------------------------------------------
# test_membership_slug_as_id_pattern (E3 Fix 1 regression)
# ---------------------------------------------------------------------------


def test_membership_slug_as_id_pattern(client: TestClient, tmp_data_dir: Path) -> None:
    """Confirm backend handles slug-as-id collection IDs (the pattern used by the
    persistent pending_collections queue introduced by E3 Fix 1).

    The extension background.ts sets id=slug for each collection in pending_collections.
    This test verifies the backend correctly stores and associates membership using
    those slug-as-id values, so a SW eviction + resume picks up where it left off.
    """
    from backend.db.connection import get_connection

    # Collections posted with slug-as-id (mirrors background.ts pending_collections)
    slug_id_collections = [
        {"id": "travel-memories", "name": "Travel Memories", "is_all_posts": False},
        {"id": "recipes-to-try", "name": "Recipes To Try", "is_all_posts": False},
    ]
    r = client.post("/api/ingest/extension/collections", json=slug_id_collections, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Shortcodes discovered during Pass-B collection iteration
    client.post(
        "/api/ingest/extension/shortcodes",
        json={
            "source": "collection",
            "collection_id": "travel-memories",
            "items": [
                {"shortcode": "trv001", "recency_rank": 0},
                {"shortcode": "trv002", "recency_rank": 1},
            ],
        },
        headers=HEADERS,
    )
    client.post(
        "/api/ingest/extension/shortcodes",
        json={
            "source": "collection",
            "collection_id": "recipes-to-try",
            "items": [
                {"shortcode": "rcp001", "recency_rank": 0},
            ],
        },
        headers=HEADERS,
    )

    # Membership POSTed with slug-as-id — mirrors what background.ts sends after each collection
    membership_batch_1 = [
        {"shortcode": "trv001", "collection_id": "travel-memories"},
        {"shortcode": "trv002", "collection_id": "travel-memories"},
    ]
    r1 = client.post("/api/ingest/extension/membership", json=membership_batch_1, headers=HEADERS)
    assert r1.status_code == 200
    assert r1.json()["upserted"] == 2

    membership_batch_2 = [
        {"shortcode": "rcp001", "collection_id": "recipes-to-try"},
    ]
    r2 = client.post("/api/ingest/extension/membership", json=membership_batch_2, headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["upserted"] == 1

    # Verify all 3 membership rows exist with correct slug-as-id collection references
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.shortcode, pc.collection_id
        FROM post_collections pc
        JOIN posts p ON p.id = pc.post_id
        ORDER BY p.shortcode
        """
    ).fetchall()
    assert len(rows) == 3
    pairs = {(row["shortcode"], row["collection_id"]) for row in rows}
    assert ("trv001", "travel-memories") in pairs
    assert ("trv002", "travel-memories") in pairs
    assert ("rcp001", "recipes-to-try") in pairs

    # Confirm collections table uses slug as primary key
    col_rows = conn.execute(
        "SELECT id FROM collections ORDER BY id"
    ).fetchall()
    col_ids = [r["id"] for r in col_rows]
    assert "recipes-to-try" in col_ids
    assert "travel-memories" in col_ids
