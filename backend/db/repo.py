"""All SQL strings live here. Plan §3 / Must-2: write-once `first_seen_at`."""

from __future__ import annotations

import sqlite3
from typing import Any

# ---------- authors ---------------------------------------------------------


def upsert_author(
    conn: sqlite3.Connection,
    *,
    author_id: str,
    username: str,
    full_name: str | None,
    is_private: bool,
    profile_pic_url: str | None,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO authors(id, username, full_name, is_private, profile_pic_url,
                            first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username        = excluded.username,
            full_name       = excluded.full_name,
            is_private      = excluded.is_private,
            profile_pic_url = excluded.profile_pic_url,
            last_seen_at    = excluded.last_seen_at
        """,
        (author_id, username, full_name, int(is_private), profile_pic_url, now, now),
    )


# ---------- posts -----------------------------------------------------------


def upsert_post(
    conn: sqlite3.Connection,
    *,
    post_id: str,
    shortcode: str,
    author_id: str,
    author_username: str,
    caption: str | None,
    media_kind: str,
    taken_at: str | None,
    saved_at: str | None,
    now: str,
) -> None:
    """Plan §3 Must-2: `first_seen_at` is written once and never updated."""
    conn.execute(
        """
        INSERT INTO posts(id, shortcode, author_id, author_username_denorm, caption,
                          media_kind, taken_at, saved_at, first_seen_at,
                          last_seen_in_saved_at, is_unsaved, is_source_deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        ON CONFLICT(id) DO UPDATE SET
            shortcode              = excluded.shortcode,
            author_id              = excluded.author_id,
            author_username_denorm = excluded.author_username_denorm,
            caption                = excluded.caption,
            media_kind             = excluded.media_kind,
            taken_at               = excluded.taken_at,
            saved_at               = excluded.saved_at,
            last_seen_in_saved_at  = excluded.last_seen_in_saved_at
        """,
        (
            post_id,
            shortcode,
            author_id,
            author_username,
            caption,
            media_kind,
            taken_at,
            saved_at,
            now,
            now,
        ),
    )


def upsert_posts_raw(conn: sqlite3.Connection, *, post_id: str, raw_json: str) -> None:
    conn.execute(
        """
        INSERT INTO posts_raw(post_id, json) VALUES (?, ?)
        ON CONFLICT(post_id) DO UPDATE SET json = excluded.json
        """,
        (post_id, raw_json),
    )


# ---------- media -----------------------------------------------------------


def upsert_media_file(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    file_path: str,
    mime_type: str | None,
    file_size_bytes: int,
    width: int | None,
    height: int | None,
    duration_seconds: float | None,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO media_files(sha256, file_path, mime_type, file_size_bytes,
                                width, height, duration_seconds, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sha256) DO NOTHING
        """,
        (
            sha256,
            file_path,
            mime_type,
            file_size_bytes,
            width,
            height,
            duration_seconds,
            fetched_at,
        ),
    )


def upsert_post_media(
    conn: sqlite3.Connection,
    *,
    post_id: str,
    media_sha256: str,
    thumbnail_sha256: str | None,
    carousel_index: int,
    media_type: str,
) -> None:
    conn.execute(
        """
        INSERT INTO post_media(post_id, media_sha256, thumbnail_sha256,
                               carousel_index, media_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(post_id, carousel_index) DO UPDATE SET
            media_sha256     = excluded.media_sha256,
            thumbnail_sha256 = excluded.thumbnail_sha256,
            media_type       = excluded.media_type
        """,
        (post_id, media_sha256, thumbnail_sha256, carousel_index, media_type),
    )


def get_media_file(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT sha256, file_path, mime_type, file_size_bytes FROM media_files WHERE sha256 = ?",
        (sha256,),
    ).fetchone()


# ---------- collections + post_collections ---------------------------------


def upsert_collection(
    conn: sqlite3.Connection,
    *,
    collection_id: str,
    name: str,
    is_all_posts: bool,
    now: str,
) -> None:
    """Plan §3 Must-2: `first_seen_at` is write-once on collections too."""
    conn.execute(
        """
        INSERT INTO collections(id, name, is_all_posts, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name         = excluded.name,
            is_all_posts = excluded.is_all_posts,
            last_seen_at = excluded.last_seen_at
        """,
        (collection_id, name, int(is_all_posts), now, now),
    )


def upsert_post_collection(
    conn: sqlite3.Connection,
    *,
    post_id: str,
    collection_id: str,
    now: str,
) -> None:
    """Plan §2/§4: `last_seen_at` is updated every full enumeration; never
    touch `added_at` on conflict."""
    conn.execute(
        """
        INSERT INTO post_collections(post_id, collection_id, added_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(post_id, collection_id) DO UPDATE SET
            last_seen_at = excluded.last_seen_at
        """,
        (post_id, collection_id, now, now),
    )


def delete_stale_post_collections(
    conn: sqlite3.Connection, *, run_started_at: str
) -> int:
    """Sweep memberships that were not refreshed by a fully-enumerated run.

    Plan §4: rows whose `last_seen_at < run.started_at` after a fully-
    enumerated run are deleted (post moved out of that collection).
    """
    cur = conn.execute(
        "DELETE FROM post_collections WHERE last_seen_at < ?",
        (run_started_at,),
    )
    return cur.rowcount or 0


def list_collections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Plan §6: returns id, name, is_all_posts, post_count, cover_post_id.

    Cover = most-recently-saved post in the collection (matches grid order).
    `is_all_posts` collections sort first.
    """
    rows = conn.execute(
        """
        SELECT
            c.id, c.name, c.is_all_posts,
            COALESCE((SELECT COUNT(*) FROM post_collections pc
                       WHERE pc.collection_id = c.id), 0) AS post_count,
            (
                SELECT pc.post_id
                  FROM post_collections pc
                  JOIN posts p ON p.id = pc.post_id
                 WHERE pc.collection_id = c.id
                   AND p.is_unsaved = 0
                 ORDER BY COALESCE(p.saved_at, p.first_seen_at) DESC, p.id DESC
                 LIMIT 1
            ) AS cover_post_id
        FROM collections c
        ORDER BY c.is_all_posts DESC, c.name COLLATE NOCASE ASC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "is_all_posts": bool(r["is_all_posts"]),
            "post_count": int(r["post_count"]),
            "cover_post_id": r["cover_post_id"],
        }
        for r in rows
    ]


# ---------- post flag mutations (reconcile) --------------------------------


def clear_post_flags(conn: sqlite3.Connection, post_id: str) -> None:
    conn.execute(
        "UPDATE posts SET is_unsaved = 0, is_source_deleted = 0 WHERE id = ?",
        (post_id,),
    )


def mark_unsaved(conn: sqlite3.Connection, post_id: str) -> None:
    conn.execute("UPDATE posts SET is_unsaved = 1 WHERE id = ?", (post_id,))


def post_ids_not_unsaved(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT id FROM posts WHERE is_unsaved = 0").fetchall()
    return {r["id"] for r in rows}


# ---------- sync_runs ------------------------------------------------------


def insert_sync_run(
    conn: sqlite3.Connection,
    *,
    started_at: str,
    triggered_by: str,
) -> int:
    """Plan §4: write a 'running' row at the start of each run."""
    cur = conn.execute(
        """
        INSERT INTO sync_runs(started_at, state, triggered_by, fully_enumerated)
        VALUES (?, 'running', ?, 0)
        """,
        (started_at, triggered_by),
    )
    return int(cur.lastrowid or 0)


def finalize_sync_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    state: str,
    finished_at: str,
    fully_enumerated: bool,
    posts_seen: int,
    posts_new: int,
    posts_unsaved: int,
    errors_json: str | None,
) -> None:
    conn.execute(
        """
        UPDATE sync_runs
           SET state = ?,
               finished_at = ?,
               fully_enumerated = ?,
               posts_seen = ?,
               posts_new = ?,
               posts_unsaved = ?,
               errors_json = ?
         WHERE id = ?
        """,
        (
            state,
            finished_at,
            int(fully_enumerated),
            posts_seen,
            posts_new,
            posts_unsaved,
            errors_json,
            run_id,
        ),
    )


def latest_sync_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the most recent sync_runs row as a dict, or None if no run
    has been recorded yet. Used by `/api/auth/status` to surface a
    `SESSION_EXPIRED` state when the latest scheduled run hit
    LoginRequired/PleaseWaitFewMinutes.
    """
    row = conn.execute(
        """
        SELECT id, started_at, finished_at, state, fully_enumerated,
               posts_seen, posts_new, posts_unsaved, triggered_by, errors_json
          FROM sync_runs
         ORDER BY id DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def post_exists(conn: sqlite3.Connection, post_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row is not None


# ---------- list query for /api/posts --------------------------------------


def list_recent_posts(
    conn: sqlite3.Connection,
    limit: int = 60,
    *,
    collection_id: str | None = None,
) -> list[dict[str, Any]]:
    """Most-recently-saved posts, descending. Optionally filter by collection.

    Per plan §10 B1, no cursor yet — straight `LIMIT 60`. Cursor lands in B3.
    """
    if collection_id is None:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.shortcode,
                p.caption,
                p.media_kind,
                p.taken_at,
                p.saved_at,
                p.first_seen_at,
                p.last_seen_in_saved_at,
                p.is_unsaved,
                p.is_source_deleted,
                p.author_username_denorm AS author_username,
                a.id          AS author_id,
                a.full_name   AS author_full_name,
                a.is_private  AS author_is_private,
                a.profile_pic_url AS author_profile_pic_url
            FROM posts p
            JOIN authors a ON a.id = p.author_id
            ORDER BY COALESCE(p.saved_at, p.first_seen_at) DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.shortcode,
                p.caption,
                p.media_kind,
                p.taken_at,
                p.saved_at,
                p.first_seen_at,
                p.last_seen_in_saved_at,
                p.is_unsaved,
                p.is_source_deleted,
                p.author_username_denorm AS author_username,
                a.id          AS author_id,
                a.full_name   AS author_full_name,
                a.is_private  AS author_is_private,
                a.profile_pic_url AS author_profile_pic_url
            FROM posts p
            JOIN authors a ON a.id = p.author_id
            JOIN post_collections pc ON pc.post_id = p.id
            WHERE pc.collection_id = ?
            ORDER BY COALESCE(p.saved_at, p.first_seen_at) DESC, p.id DESC
            LIMIT ?
            """,
            (collection_id, limit),
        ).fetchall()

    if not rows:
        return []

    post_ids = tuple(r["id"] for r in rows)
    placeholders = ",".join("?" * len(post_ids))
    media_rows = conn.execute(
        f"""
        SELECT pm.post_id, pm.carousel_index, pm.media_type,
               pm.media_sha256, pm.thumbnail_sha256,
               mf.width, mf.height, mf.duration_seconds
        FROM post_media pm
        JOIN media_files mf ON mf.sha256 = pm.media_sha256
        WHERE pm.post_id IN ({placeholders})
        ORDER BY pm.post_id, pm.carousel_index
        """,
        post_ids,
    ).fetchall()

    by_post: dict[str, list[dict[str, Any]]] = {pid: [] for pid in post_ids}
    for mr in media_rows:
        by_post[mr["post_id"]].append(
            {
                "sha256": mr["media_sha256"],
                "thumbnail_sha256": mr["thumbnail_sha256"],
                "media_type": mr["media_type"],
                "carousel_index": mr["carousel_index"],
                "width": mr["width"],
                "height": mr["height"],
                "duration_seconds": mr["duration_seconds"],
            }
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "shortcode": r["shortcode"],
                "caption": r["caption"],
                "media_kind": r["media_kind"],
                "taken_at": r["taken_at"],
                "saved_at": r["saved_at"],
                "first_seen_at": r["first_seen_at"],
                "last_seen_in_saved_at": r["last_seen_in_saved_at"],
                "is_unsaved": bool(r["is_unsaved"]),
                "is_source_deleted": bool(r["is_source_deleted"]),
                "author": {
                    "id": r["author_id"],
                    "username": r["author_username"],
                    "full_name": r["author_full_name"],
                    "is_private": bool(r["author_is_private"]),
                    "profile_pic_url": r["author_profile_pic_url"],
                },
                "slides": by_post.get(r["id"], []),
            }
        )
    return out


def get_post_detail(
    conn: sqlite3.Connection, post_id: str
) -> dict[str, Any] | None:
    """Single-post detail for `GET /api/posts/{id}`. Includes the M:M
    collection list so the modal can render which collections this post
    belongs to. Returns None when the post is unknown.
    """
    row = conn.execute(
        """
        SELECT
            p.id,
            p.shortcode,
            p.caption,
            p.media_kind,
            p.taken_at,
            p.saved_at,
            p.first_seen_at,
            p.last_seen_in_saved_at,
            p.is_unsaved,
            p.is_source_deleted,
            p.author_username_denorm AS author_username,
            a.id              AS author_id,
            a.full_name       AS author_full_name,
            a.is_private      AS author_is_private,
            a.profile_pic_url AS author_profile_pic_url
        FROM posts p
        JOIN authors a ON a.id = p.author_id
        WHERE p.id = ?
        """,
        (post_id,),
    ).fetchone()
    if row is None:
        return None

    media_rows = conn.execute(
        """
        SELECT pm.carousel_index, pm.media_type,
               pm.media_sha256, pm.thumbnail_sha256,
               mf.width, mf.height, mf.duration_seconds
        FROM post_media pm
        JOIN media_files mf ON mf.sha256 = pm.media_sha256
        WHERE pm.post_id = ?
        ORDER BY pm.carousel_index
        """,
        (post_id,),
    ).fetchall()
    slides = [
        {
            "sha256": mr["media_sha256"],
            "thumbnail_sha256": mr["thumbnail_sha256"],
            "media_type": mr["media_type"],
            "carousel_index": mr["carousel_index"],
            "width": mr["width"],
            "height": mr["height"],
            "duration_seconds": mr["duration_seconds"],
        }
        for mr in media_rows
    ]

    coll_rows = conn.execute(
        """
        SELECT c.id, c.name, c.is_all_posts
        FROM post_collections pc
        JOIN collections c ON c.id = pc.collection_id
        WHERE pc.post_id = ?
        ORDER BY c.is_all_posts DESC, c.name ASC
        """,
        (post_id,),
    ).fetchall()
    collections = [
        {
            "id": cr["id"],
            "name": cr["name"],
            "is_all_posts": bool(cr["is_all_posts"]),
        }
        for cr in coll_rows
    ]

    return {
        "id": row["id"],
        "shortcode": row["shortcode"],
        "caption": row["caption"],
        "media_kind": row["media_kind"],
        "taken_at": row["taken_at"],
        "saved_at": row["saved_at"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_in_saved_at": row["last_seen_in_saved_at"],
        "is_unsaved": bool(row["is_unsaved"]),
        "is_source_deleted": bool(row["is_source_deleted"]),
        "author": {
            "id": row["author_id"],
            "username": row["author_username"],
            "full_name": row["author_full_name"],
            "is_private": bool(row["author_is_private"]),
            "profile_pic_url": row["author_profile_pic_url"],
        },
        "slides": slides,
        "collections": collections,
    }
