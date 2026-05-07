"""Integration tests for `backend.ingest.reconcile.run_once`.

Uses the fake IG client + tmp SQLite. Asserts every behavior the brief
calls out under Part B step 4 (a–e).
"""

from __future__ import annotations


def _count(conn, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def test_fresh_db_inserts_all_fixtures(tmp_data_dir):
    """(a) Fresh DB + fake fixture → 40 posts, 4 collections, M:M correct."""
    from backend.db.connection import get_connection
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once

    fake = FakeIgClient()
    run_id = run_once(client=fake, triggered_by="manual")
    conn = get_connection()

    assert _count(conn, "SELECT COUNT(*) FROM posts") == 40
    assert _count(conn, "SELECT COUNT(*) FROM collections") == 4
    # 40 + 8 + 5 + 12 = 65 join rows.
    assert _count(conn, "SELECT COUNT(*) FROM post_collections") == 65

    # Multi-membership: 6 posts must appear in 2+ named collections.
    rows = conn.execute(
        """
        SELECT post_id, COUNT(*) AS n
        FROM post_collections
        WHERE collection_id != 'all_posts'
        GROUP BY post_id
        HAVING n >= 2
        """
    ).fetchall()
    assert len(rows) == 6

    # sync_runs row written and is fully_enumerated=1, state='ok'.
    run_row = conn.execute(
        "SELECT state, fully_enumerated, posts_seen, posts_new, triggered_by, "
        "started_at, finished_at FROM sync_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert run_row["state"] == "ok"
    assert run_row["fully_enumerated"] == 1
    assert run_row["posts_seen"] == 40
    assert run_row["posts_new"] == 40
    assert run_row["triggered_by"] == "manual"
    assert run_row["started_at"] is not None
    assert run_row["finished_at"] is not None


def test_idempotent_second_run(tmp_data_dir):
    """(b) Second run with same fixture → no new posts, no false unsaves."""
    from backend.db.connection import get_connection
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once

    fake = FakeIgClient()
    run_once(client=fake, triggered_by="manual")
    first_seen_before = dict(
        conn_row
        for conn_row in get_connection().execute(
            "SELECT id, first_seen_at FROM posts"
        ).fetchall()
    )

    run_id_2 = run_once(client=fake, triggered_by="manual")
    conn = get_connection()

    assert _count(conn, "SELECT COUNT(*) FROM posts") == 40
    assert _count(conn, "SELECT COUNT(*) FROM post_collections") == 65

    # No false unsaves.
    assert _count(conn, "SELECT COUNT(*) FROM posts WHERE is_unsaved = 1") == 0

    # `first_seen_at` is write-once (Must-2).
    for pid, fs in first_seen_before.items():
        row = conn.execute(
            "SELECT first_seen_at FROM posts WHERE id = ?", (pid,)
        ).fetchone()
        assert row["first_seen_at"] == fs

    # Second sync_run row: posts_new == 0 and posts_seen == 40.
    run_row = conn.execute(
        "SELECT posts_new, posts_seen FROM sync_runs WHERE id = ?", (run_id_2,)
    ).fetchone()
    assert run_row["posts_new"] == 0
    assert run_row["posts_seen"] == 40


def test_removed_posts_get_unsaved(tmp_data_dir):
    """(c) Two posts vanish on second run + fully_enumerated=true → is_unsaved=1."""
    from backend.db.connection import get_connection
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once

    fake = FakeIgClient()
    run_once(client=fake, triggered_by="manual")

    fake.remove_posts(["p0001", "p0002"])
    run_once(client=fake, triggered_by="manual")
    conn = get_connection()

    unsaved_ids = {
        r["id"]
        for r in conn.execute(
            "SELECT id FROM posts WHERE is_unsaved = 1"
        ).fetchall()
    }
    assert unsaved_ids == {"p0001", "p0002"}

    # Total post count unchanged — they're flagged, not deleted.
    assert _count(conn, "SELECT COUNT(*) FROM posts") == 40

    # The removed posts also lose their post_collections rows because the
    # sweep deletes stale memberships after a fully-enumerated run.
    assert (
        _count(
            conn,
            "SELECT COUNT(*) FROM post_collections WHERE post_id IN (?, ?)",
            ("p0001", "p0002"),
        )
        == 0
    )


def test_resave_clears_unsaved(tmp_data_dir):
    """(d) Re-save brings them back → is_unsaved cleared on third run."""
    from backend.db.connection import get_connection
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once

    fake = FakeIgClient()
    run_once(client=fake, triggered_by="manual")
    fake.remove_posts(["p0001", "p0002"])
    run_once(client=fake, triggered_by="manual")
    fake.restore_posts(["p0001", "p0002"])
    run_once(client=fake, triggered_by="manual")
    conn = get_connection()

    assert _count(conn, "SELECT COUNT(*) FROM posts WHERE is_unsaved = 1") == 0

    # Memberships are restored too.
    rows = conn.execute(
        "SELECT collection_id FROM post_collections WHERE post_id = 'p0001'"
    ).fetchall()
    cids = {r["collection_id"] for r in rows}
    # p0001 lives in All Posts + Travel.
    assert "all_posts" in cids
    assert "col_trav" in cids


def test_collection_membership_removal(tmp_data_dir):
    """(e) Remove a post from "Inspiration" → post_collections row gone."""
    from backend.db.connection import get_connection
    from backend.ig_client.fake import FakeIgClient
    from backend.ingest.reconcile import run_once

    fake = FakeIgClient()
    run_once(client=fake, triggered_by="manual")
    conn = get_connection()

    # Sanity: p0000 starts in Inspiration.
    inspiration = {
        r["post_id"]
        for r in conn.execute(
            "SELECT post_id FROM post_collections WHERE collection_id = 'col_insp'"
        ).fetchall()
    }
    assert "p0000" in inspiration

    fake.remove_from_collection("Inspiration", ["p0000"])
    run_once(client=fake, triggered_by="manual")

    inspiration_after = {
        r["post_id"]
        for r in conn.execute(
            "SELECT post_id FROM post_collections WHERE collection_id = 'col_insp'"
        ).fetchall()
    }
    assert "p0000" not in inspiration_after

    # Post is still on disk and not flagged unsaved (it remains in All Posts).
    row = conn.execute(
        "SELECT is_unsaved FROM posts WHERE id = 'p0000'"
    ).fetchone()
    assert row is not None
    assert row["is_unsaved"] == 0
