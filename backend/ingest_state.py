"""Derives ingest phase + next targets + counts from the DB.

Used by GET /api/ingest/extension/state.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def get_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the full state payload for GET /api/ingest/extension/state.

    Shape per plan §5.1:
    {
        phase_suggestion: str,
        total_discovered: int,
        total_enriched: int,
        total_lost: int,
        total_placeholder: int,
        next_enrichment_target: {shortcode} | None,
        next_retry_target: {shortcode, reason} | None,
        priority_target: {shortcode, reason} | None,  (alias for next_retry_target)
        collections_known: [{id, name, last_seen_at}],
        last_logged_out_at: str | None,
    }
    """
    # Aggregate counts
    counts_rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(state = 'enriched')    AS enriched,
            SUM(state = 'lost')        AS lost,
            SUM(state = 'placeholder') AS placeholder
        FROM posts
        """
    ).fetchone()
    total_discovered = int(counts_rows["total"] or 0)
    total_enriched = int(counts_rows["enriched"] or 0)
    total_lost = int(counts_rows["lost"] or 0)
    total_placeholder = int(counts_rows["placeholder"] or 0)

    phase_suggestion = derive_phase_suggestion(conn)
    next_enrichment = next_enrichment_target(conn)
    next_retry = next_retry_target(conn)

    # Collections list
    coll_rows = conn.execute(
        """
        SELECT id, name, last_seen_at
        FROM collections
        ORDER BY is_all_posts DESC, name COLLATE NOCASE ASC
        """
    ).fetchall()
    collections_known = [
        {
            "id": r["id"],
            "name": r["name"],
            "last_seen_at": r["last_seen_at"],
        }
        for r in coll_rows
    ]

    # ingest_meta (may not exist yet — defensive read)
    last_logged_out_at: str | None = None
    meta_row = conn.execute(
        "SELECT last_logged_out_at FROM ingest_meta WHERE id = 1"
    ).fetchone()
    if meta_row is not None:
        last_logged_out_at = meta_row["last_logged_out_at"]

    return {
        "phase_suggestion": phase_suggestion,
        "total_discovered": total_discovered,
        "total_enriched": total_enriched,
        "total_lost": total_lost,
        "total_placeholder": total_placeholder,
        "next_enrichment_target": (
            {"shortcode": next_enrichment} if next_enrichment else None
        ),
        "next_retry_target": next_retry,
        "priority_target": next_retry,
        "collections_known": collections_known,
        "last_logged_out_at": last_logged_out_at,
    }


def derive_phase_suggestion(conn: sqlite3.Connection) -> str:
    """Return one of: idle | discovery_all | discovery_collections | enrichment | watch.

    Rules (plan §5.1 / ingest_state.py spec):
    - 0 posts → discovery_all
    - posts exist but 0 collections → discovery_collections
    - any posts with state='placeholder' AND retry_count < 3 → enrichment
    - all posts in (enriched | lost) → watch
    """
    post_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if post_count == 0:
        return "discovery_all"

    coll_count = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    if coll_count == 0:
        return "discovery_collections"

    actionable_placeholder = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE state = 'placeholder' AND retry_count < 3"
    ).fetchone()[0]
    if actionable_placeholder > 0:
        return "enrichment"

    return "watch"


def next_enrichment_target(conn: sqlite3.Connection) -> str | None:
    """Return the shortcode of the oldest unenriched post, or None.

    SQL per plan §4.5 / §5.1: placeholder rows with retry_count < 3,
    due for retry (next_retry_at IS NULL OR <= now), ordered oldest-first
    by recency_rank DESC (higher rank = older post).
    """
    row = conn.execute(
        """
        SELECT shortcode
        FROM posts
        WHERE state = 'placeholder'
          AND retry_count < 3
          AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
        ORDER BY recency_rank DESC
        LIMIT 1
        """
    ).fetchone()
    return row["shortcode"] if row else None


def next_retry_target(conn: sqlite3.Connection) -> dict[str, str] | None:
    """Read and clear the one-shot priority_target from ingest_meta.

    Per plan §4.9: one-shot semantics — clears after read so it surfaces
    exactly once in the next /state response.
    """
    row = conn.execute(
        """
        SELECT priority_target_post_id, priority_target_reason
        FROM ingest_meta
        WHERE id = 1
          AND priority_target_post_id IS NOT NULL
        """
    ).fetchone()
    if row is None:
        return None

    post_id = row["priority_target_post_id"]
    reason = row["priority_target_reason"]

    # Resolve post_id → shortcode for the response shape
    post_row = conn.execute(
        "SELECT shortcode FROM posts WHERE id = ?", (post_id,)
    ).fetchone()

    # Clear the one-shot target
    conn.execute(
        """
        UPDATE ingest_meta
        SET priority_target_post_id = NULL, priority_target_reason = NULL
        WHERE id = 1
        """
    )

    if post_row is None:
        return None

    return {"shortcode": post_row["shortcode"], "reason": reason}
