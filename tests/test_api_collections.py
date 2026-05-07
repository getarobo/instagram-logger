"""Contract tests for /api/collections and /api/posts?collection_id=…

Spins up a FastAPI TestClient, runs an offline sync via the fake IG client,
then asserts the JSON shapes match plan §6 / §10 B1.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _seeded_app(tmp_data_dir):
    """Run a fake sync, then return an app + TestClient bound to that DB."""
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once
    from backend.main import app

    fake = FakeIgClient()
    run_once(client=fake, triggered_by="manual")

    return TestClient(app)


def test_list_collections_shape(tmp_data_dir):
    client = _seeded_app(tmp_data_dir)
    resp = client.get("/api/collections")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    items = body["items"]
    assert isinstance(items, list)
    assert len(items) == 4

    # is_all_posts comes first.
    assert items[0]["is_all_posts"] is True
    assert items[0]["id"] == "all_posts"
    assert items[0]["post_count"] == 40
    assert items[0]["cover_post_id"] is not None

    by_id = {it["id"]: it for it in items}
    assert by_id["col_insp"]["post_count"] == 8
    assert by_id["col_rec"]["post_count"] == 5
    assert by_id["col_trav"]["post_count"] == 12

    # Every item has the locked field set.
    for it in items:
        assert set(it.keys()) == {
            "id",
            "name",
            "is_all_posts",
            "post_count",
            "cover_post_id",
        }


def test_posts_no_filter_returns_60_cap(tmp_data_dir):
    client = _seeded_app(tmp_data_dir)
    resp = client.get("/api/posts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["next_cursor"] is None
    # Fixture has 40 posts so no LIMIT clipping.
    assert len(body["items"]) == 40

    # Locked Post shape (a subset; full check left to B2 contract test).
    sample = body["items"][0]
    for key in (
        "id",
        "shortcode",
        "caption",
        "media_kind",
        "taken_at",
        "saved_at",
        "first_seen_at",
        "last_seen_in_saved_at",
        "is_unsaved",
        "is_source_deleted",
        "author",
        "slides",
    ):
        assert key in sample, f"missing key {key} in /api/posts payload"
    assert isinstance(sample["author"], dict)
    assert isinstance(sample["slides"], list)


def test_posts_filtered_by_collection(tmp_data_dir):
    client = _seeded_app(tmp_data_dir)

    resp = client.get("/api/posts", params={"collection_id": "col_rec"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5

    resp = client.get("/api/posts", params={"collection_id": "col_trav"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 12

    # Unknown collection -> empty list, still 200.
    resp = client.get("/api/posts", params={"collection_id": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
