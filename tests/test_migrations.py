"""Group A — Migration tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_migrations_apply_in_order(tmp_data_dir: Path) -> None:
    """All three migrations must be recorded in schema_migrations."""
    from backend.db.connection import get_connection

    conn = get_connection()
    versions = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    assert 1 in versions
    assert 2 in versions
    assert 3 in versions


def test_migration_002_adds_columns(tmp_data_dir: Path) -> None:
    """Migration 002 must add state/recency_rank/slides_* to posts and state to post_media."""
    from backend.db.connection import get_connection

    conn = get_connection()

    posts_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(posts)").fetchall()
    }
    for col in ("state", "recency_rank", "slides_total", "slides_present", "slides_failed"):
        assert col in posts_cols, f"posts.{col} missing"

    media_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(post_media)").fetchall()
    }
    assert "state" in media_cols, "post_media.state missing"


def test_migration_002_aggregate_triggers(tmp_data_dir: Path) -> None:
    """INSERT/UPDATE/DELETE on post_media must maintain posts slide aggregates."""
    from backend.db.connection import get_connection

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"

    # Seed author + post
    conn.execute(
        "INSERT OR IGNORE INTO authors(id, username, full_name, is_private, "
        "profile_pic_url, first_seen_at, last_seen_at) "
        "VALUES ('a1', 'user1', NULL, 0, NULL, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO posts(id, shortcode, author_id, author_username_denorm, "
        "caption, media_kind, taken_at, saved_at, first_seen_at, "
        "last_seen_in_saved_at, is_unsaved, is_source_deleted, state) "
        "VALUES ('p1', 'sc1', 'a1', 'user1', NULL, 'image', NULL, NULL, ?, ?, 0, 0, 'placeholder')",
        (now, now),
    )

    # Seed a placeholder media_files row so the FK on post_media.media_sha256 is satisfied.
    # post_media requires media_sha256 to reference media_files(sha256).
    placeholder_sha = "0" * 64
    conn.execute(
        "INSERT OR IGNORE INTO media_files(sha256, file_path, mime_type, "
        "file_size_bytes, fetched_at) VALUES (?, 'placeholder', NULL, 0, ?)",
        (placeholder_sha, now),
    )

    # Insert 3 pending slides → slides_total = 3, present = 0
    for i in range(3):
        conn.execute(
            "INSERT INTO post_media(post_id, media_sha256, thumbnail_sha256, "
            "carousel_index, media_type, state) "
            "VALUES ('p1', ?, NULL, ?, 'image', 'pending')",
            (placeholder_sha, i),
        )

    row = conn.execute(
        "SELECT slides_total, slides_present, slides_failed FROM posts WHERE id = 'p1'"
    ).fetchone()
    assert row["slides_total"] == 3
    assert row["slides_present"] == 0
    assert row["slides_failed"] == 0

    # Update slide 0 → present
    conn.execute(
        "UPDATE post_media SET state = 'present' WHERE post_id = 'p1' AND carousel_index = 0"
    )
    row = conn.execute(
        "SELECT slides_total, slides_present, slides_failed FROM posts WHERE id = 'p1'"
    ).fetchone()
    assert row["slides_present"] == 1
    assert row["slides_failed"] == 0

    # Update slide 1 → media_failed
    conn.execute(
        "UPDATE post_media SET state = 'media_failed' WHERE post_id = 'p1' AND carousel_index = 1"
    )
    row = conn.execute(
        "SELECT slides_total, slides_present, slides_failed FROM posts WHERE id = 'p1'"
    ).fetchone()
    assert row["slides_failed"] == 1
    assert row["slides_present"] == 1  # unchanged

    # Delete slide 2 (pending) → slides_total = 2
    conn.execute(
        "DELETE FROM post_media WHERE post_id = 'p1' AND carousel_index = 2"
    )
    row = conn.execute(
        "SELECT slides_total, slides_present, slides_failed FROM posts WHERE id = 'p1'"
    ).fetchone()
    assert row["slides_total"] == 2


def test_migration_atomicity(tmp_path: Path) -> None:
    """Pre-inserting the `state` column before 002 causes a duplicate-column error
    mid-migration, leaving the schema in its pre-migration baseline state.

    SQLite's executescript() commits each statement as it goes, so true rollback of
    already-executed DDL is not possible; this test verifies that the migrate runner
    does NOT mark the version as applied when the script raises an error partway through.
    """
    import shutil

    from backend.config import settings
    from backend.db import connection as db_connection
    from backend.db.migrate import apply_migrations

    # Build a modified migrations dir that has only 001 + a broken 002
    mig_src = settings.migrations_dir
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()

    # Copy 001 as-is
    shutil.copy(mig_src / "001_init.sql", mig_dir / "001_init.sql")

    # Build a broken 002: pre-inject a duplicate column that 002 would add.
    # The duplicate column error will fire during executescript.
    broken_sql = (
        "PRAGMA foreign_keys = ON;\nBEGIN;\n"
        "ALTER TABLE posts ADD COLUMN state TEXT;\n"  # first add
        "ALTER TABLE posts ADD COLUMN state TEXT;\n"  # duplicate → error
        "COMMIT;\n"
        "INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'));\n"
    )
    (mig_dir / "002_broken.sql").write_text(broken_sql)

    # Fresh tmp data dir
    data = tmp_path / "atomicity_data"
    data.mkdir()
    original_data = settings.data_dir
    settings.data_dir = data
    db_connection.close_thread_connection()

    try:
        # Apply migrations; 001 succeeds; 002 broken script will raise
        with pytest.raises(Exception):  # noqa: B017
            apply_migrations(migrations_dir=mig_dir)

        # Check schema_migrations: version 2 must NOT be recorded
        conn = db_connection.get_connection()
        versions = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert 2 not in versions, "version 2 should not be marked applied after failure"
    finally:
        db_connection.close_thread_connection()
        settings.data_dir = original_data
