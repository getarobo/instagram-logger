"""Group D — ingest_state.py derivation tests."""

from __future__ import annotations

from pathlib import Path


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
    recency_rank: int | None = None,
    retry_count: int = 0,
    now: str = "2026-01-01T00:00:00+00:00",
) -> None:
    conn.execute(
        "INSERT INTO posts(id, shortcode, author_id, author_username_denorm, "
        "caption, media_kind, taken_at, saved_at, first_seen_at, "
        "last_seen_in_saved_at, is_unsaved, is_source_deleted, "
        "state, recency_rank, retry_count) "
        "VALUES (?, ?, 'unknown', 'unknown', NULL, 'unknown', NULL, NULL, ?, ?, 0, 0, ?, ?, ?)",
        (post_id, shortcode, now, now, state, recency_rank, retry_count),
    )


def test_phase_idle_when_empty(tmp_data_dir: Path) -> None:
    """Empty DB → phase suggestion is 'discovery_all'."""
    from backend.db.connection import get_connection
    from backend.ingest_state import derive_phase_suggestion

    conn = get_connection()
    assert derive_phase_suggestion(conn) == "discovery_all"


def test_phase_enrichment_when_placeholders_exist(tmp_data_dir: Path) -> None:
    """Posts in 'placeholder' state → phase suggestion is 'enrichment'."""
    from backend.db.connection import get_connection
    from backend.ingest_state import derive_phase_suggestion

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"
    _seed_author(conn, now)

    # Need at least one collection so we don't get discovery_collections
    conn.execute(
        "INSERT INTO collections(id, name, is_all_posts, first_seen_at, last_seen_at) "
        "VALUES ('c1', 'All Posts', 1, ?, ?)",
        (now, now),
    )

    _insert_post(conn, "p1", "sc1", "placeholder", recency_rank=0, retry_count=0, now=now)
    _insert_post(conn, "p2", "sc2", "placeholder", recency_rank=1, retry_count=0, now=now)

    assert derive_phase_suggestion(conn) == "enrichment"


def test_phase_watch_when_all_terminal(tmp_data_dir: Path) -> None:
    """All posts in enriched or lost → phase suggestion is 'watch'."""
    from backend.db.connection import get_connection
    from backend.ingest_state import derive_phase_suggestion

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"
    _seed_author(conn, now)

    conn.execute(
        "INSERT INTO collections(id, name, is_all_posts, first_seen_at, last_seen_at) "
        "VALUES ('c1', 'All Posts', 1, ?, ?)",
        (now, now),
    )

    _insert_post(conn, "p1", "sc1", "enriched", now=now)
    _insert_post(conn, "p2", "sc2", "lost", now=now)

    assert derive_phase_suggestion(conn) == "watch"


def test_next_enrichment_target_oldest_first(tmp_data_dir: Path) -> None:
    """next_enrichment_target returns shortcode with highest recency_rank (oldest)."""
    from backend.db.connection import get_connection
    from backend.ingest_state import next_enrichment_target

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"
    _seed_author(conn, now)

    _insert_post(conn, "p0", "sc_newest", "placeholder", recency_rank=0, now=now)
    _insert_post(conn, "p1", "sc_middle", "placeholder", recency_rank=1, now=now)
    _insert_post(conn, "p2", "sc_oldest", "placeholder", recency_rank=2, now=now)

    result = next_enrichment_target(conn)
    assert result == "sc_oldest"


def test_next_retry_target_one_shot(tmp_data_dir: Path) -> None:
    """next_retry_target reads the priority target once and clears it."""
    from backend.db.connection import get_connection
    from backend.ingest_state import next_retry_target

    conn = get_connection()
    now = "2026-01-01T00:00:00+00:00"
    _seed_author(conn, now)

    # Insert the post that will be the priority target
    _insert_post(conn, "abc", "abc_shortcode", "placeholder", now=now)

    # Set priority in ingest_meta
    conn.execute(
        "UPDATE ingest_meta SET priority_target_post_id = 'abc', "
        "priority_target_reason = 'manual_retry_page' WHERE id = 1"
    )

    first = next_retry_target(conn)
    assert first is not None
    assert first["shortcode"] == "abc_shortcode"
    assert first["reason"] == "manual_retry_page"

    # Second call → cleared → None
    second = next_retry_target(conn)
    assert second is None
