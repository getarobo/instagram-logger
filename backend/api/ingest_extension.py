"""Ingest extension API endpoints.

All /api/ingest/extension/* endpoints are secret-gated via X-Ingest-Secret.
Retry endpoints (/api/posts/:id/retry-*) are NOT secret-gated (loopback frontend).

Plan §5.1 / consensus §3.1 E1 scope.
"""

from __future__ import annotations

import hmac
import json
import re as _re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import APIKeyHeader

from backend.config import settings
from backend.db.connection import get_connection, tx_immediate
from backend.ingest_state import get_state
from backend.media.from_upload import ShaMismatchError, UploadTooLargeError, store_upload
from backend.notify import telegram

router = APIRouter()

# ---------------------------------------------------------------------------
# Input validation (consensus M2)
# ---------------------------------------------------------------------------

_SHORTCODE_RE = _re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_SLUG_RE = _re.compile(r'^[A-Za-z0-9_-]{1,128}$')


def _validate_shortcode(value: str, field: str = "shortcode") -> None:
    """Raise 400 if value contains invalid characters."""
    if not _SHORTCODE_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"invalid {field}: {value!r} — must match [A-Za-z0-9_-]{{1,128}}",
        )


def _validate_slug(value: str, field: str = "id") -> None:
    """Raise 400 if slug contains invalid characters."""
    if not _SLUG_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"invalid {field}: {value!r} — must match [A-Za-z0-9_-]{{1,128}}",
        )

# ---------------------------------------------------------------------------
# Secret gate
# ---------------------------------------------------------------------------

_SECRET_HEADER = APIKeyHeader(name="X-Ingest-Secret", auto_error=False)


def require_ingest_secret(
    token: Annotated[str | None, Depends(_SECRET_HEADER)],
) -> None:
    """Constant-time compare X-Ingest-Secret against settings.ingest_secret."""
    configured = settings.ingest_secret
    if not configured:
        raise HTTPException(status_code=401, detail="ingest_secret not configured")
    if token is None:
        raise HTTPException(status_code=401, detail="X-Ingest-Secret header missing")
    # constant-time compare; encode both to bytes
    if not hmac.compare_digest(token.encode(), configured.encode()):
        raise HTTPException(status_code=401, detail="invalid ingest secret")


SecretGate = Annotated[None, Depends(require_ingest_secret)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _get_meta(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Defensive read of ingest_meta; returns None if 003 migration not applied."""
    try:
        return conn.execute("SELECT * FROM ingest_meta WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None


def _rate_limited(conn: sqlite3.Connection) -> bool:
    """Return True if a critical alert was sent within the last 30 minutes."""
    meta = _get_meta(conn)
    if meta is None or meta["last_alert_at"] is None:
        return False
    last = datetime.fromisoformat(meta["last_alert_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last) < timedelta(minutes=30)


def _update_alert_ts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE ingest_meta SET last_alert_at = ? WHERE id = 1",
        (_now_iso(),),
    )


# ---------------------------------------------------------------------------
# GET /api/ingest/extension/state
# ---------------------------------------------------------------------------


@router.get("/ingest/extension/state")
def extension_state(_: SecretGate) -> dict[str, Any]:
    conn = get_connection()
    return get_state(conn)


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/collections
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/collections")
def ingest_collections(_: SecretGate, body: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert a list of collections: [{id, name, is_all_posts}]."""
    now = _now_iso()
    # M2: validate collection IDs
    for item in body:
        _validate_slug(str(item.get("id", "")), field="id")
    with tx_immediate() as conn:
        for item in body:
            conn.execute(
                """
                INSERT INTO collections(id, name, is_all_posts, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name         = excluded.name,
                    is_all_posts = excluded.is_all_posts,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item["id"],
                    item["name"],
                    int(bool(item.get("is_all_posts", False))),
                    now,
                    now,
                ),
            )
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/shortcodes
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/shortcodes")
def ingest_shortcodes(_: SecretGate, body: dict[str, Any]) -> dict[str, Any]:
    """Insert placeholder posts with recency_rank.

    Body: {source: 'all_posts'|'collection', collection_id?: str,
           items: [{shortcode, recency_rank, thumb_url?, position?}]}
    """
    collection_id: str | None = body.get("collection_id")
    items: list[dict[str, Any]] = body.get("items", [])
    now = _now_iso()

    # M2: validate shortcodes and optional collection_id
    for item in items:
        _validate_shortcode(str(item.get("shortcode", "")))
    if collection_id is not None:
        _validate_slug(collection_id, field="collection_id")

    with tx_immediate() as conn:
        # Ensure 'unknown' author exists before any post insert (FK constraint).
        conn.execute(
            """
            INSERT OR IGNORE INTO authors(id, username, full_name, is_private,
                                          profile_pic_url, first_seen_at, last_seen_at)
            VALUES ('unknown', 'unknown', NULL, 0, NULL, ?, ?)
            """,
            (now, now),
        )

        for item in items:
            shortcode = item["shortcode"]
            recency_rank = item.get("recency_rank")
            # Use shortcode as a stable post_id for placeholder rows.
            # Real post_id (IG numeric ID) is filled in during enrichment.
            post_id = shortcode

            # Insert placeholder post (conflict = no-op; don't overwrite enriched state)
            conn.execute(
                """
                INSERT INTO posts(
                    id, shortcode, author_id, author_username_denorm,
                    caption, media_kind, taken_at, saved_at,
                    first_seen_at, last_seen_in_saved_at,
                    is_unsaved, is_source_deleted,
                    recency_rank, state, retry_count
                )
                SELECT
                    ?, ?, 'unknown', 'unknown',
                    NULL, 'unknown', NULL, NULL,
                    ?, ?,
                    0, 0,
                    ?, 'placeholder', 0
                WHERE NOT EXISTS (SELECT 1 FROM posts WHERE shortcode = ?)
                """,
                (post_id, shortcode, now, now, recency_rank, shortcode),
            )

            if collection_id:
                # Resolve actual post_id from shortcode
                row = conn.execute(
                    "SELECT id FROM posts WHERE shortcode = ?", (shortcode,)
                ).fetchone()
                if row:
                    pid = row["id"]
                    conn.execute(
                        """
                        INSERT INTO post_collections(post_id, collection_id, added_at, last_seen_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(post_id, collection_id) DO UPDATE SET
                            last_seen_at = excluded.last_seen_at
                        """,
                        (pid, collection_id, now, now),
                    )

    return {"ok": True, "count": len(items)}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/membership
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/membership")
def ingest_membership(_: SecretGate, body: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert post_collections: [{shortcode, collection_id}]."""
    now = _now_iso()
    # M2: validate shortcodes and collection_ids
    for item in body:
        _validate_shortcode(str(item.get("shortcode", "")))
        _validate_slug(str(item.get("collection_id", "")), field="collection_id")
    upserted = 0
    with tx_immediate() as conn:
        for item in body:
            shortcode = item["shortcode"]
            collection_id = item["collection_id"]
            row = conn.execute(
                "SELECT id FROM posts WHERE shortcode = ?", (shortcode,)
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                """
                INSERT INTO post_collections(post_id, collection_id, added_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(post_id, collection_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (row["id"], collection_id, now, now),
            )
            upserted += 1
    return {"ok": True, "upserted": upserted}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/post
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/post")
def ingest_post(_: SecretGate, body: dict[str, Any]) -> dict[str, Any]:
    """Upsert a post outcome: enriched or lost.

    Body: {shortcode, outcome: 'enriched'|'lost', ...}
    """
    shortcode: str = body["shortcode"]
    outcome: str = body["outcome"]
    now = _now_iso()

    # M2: validate shortcode
    _validate_shortcode(shortcode)

    with tx_immediate() as conn:
        existing = conn.execute(
            "SELECT id, state FROM posts WHERE shortcode = ?", (shortcode,)
        ).fetchone()

        if outcome == "lost":
            # 7-day sanity recheck (one-shot)
            next_retry = (datetime.now(UTC) + timedelta(days=7)).isoformat(
                timespec="seconds"
            )
            if existing:
                conn.execute(
                    """
                    UPDATE posts
                    SET state = 'lost',
                        last_attempted_at = ?,
                        next_retry_at = ?
                    WHERE id = ?
                    """,
                    (now, next_retry, existing["id"]),
                )
            else:
                # Insert as lost placeholder (rare: post not yet discovered)
                _ensure_unknown_author(conn, now)
                conn.execute(
                    """
                    INSERT INTO posts(
                        id, shortcode, author_id, author_username_denorm,
                        caption, media_kind, taken_at, saved_at,
                        first_seen_at, last_seen_in_saved_at,
                        is_unsaved, is_source_deleted,
                        state, last_attempted_at, next_retry_at
                    ) VALUES (?, ?, 'unknown', 'unknown',
                              NULL, 'unknown', NULL, NULL,
                              ?, ?,
                              0, 0,
                              'lost', ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (shortcode, shortcode, now, now, now, next_retry),
                )

        elif outcome == "enriched":
            author_data: dict[str, Any] = body.get("author", {})
            author_id: str = author_data.get("id", shortcode + "_author")
            author_username: str = author_data.get("username", "unknown")
            author_full_name: str | None = author_data.get("full_name")
            author_is_private: bool = bool(author_data.get("is_private", False))
            author_pic_url: str | None = author_data.get("avatar_url") or author_data.get(
                "profile_pic_url"
            )

            caption: str | None = body.get("caption")
            taken_at: str | None = body.get("taken_at")
            media_kind: str = body.get("media_kind", "unknown")
            raw_html_snippet: str | None = body.get("raw_html_snippet")
            slides: list[dict[str, Any]] = body.get("slides", [])

            # Use numeric post ID from payload if provided, else fall back to shortcode
            post_id: str = body.get("post_id") or (
                existing["id"] if existing else shortcode
            )

            # Upsert author
            conn.execute(
                """
                INSERT INTO authors(id, username, full_name, is_private,
                                    profile_pic_url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username        = excluded.username,
                    full_name       = excluded.full_name,
                    is_private      = excluded.is_private,
                    profile_pic_url = excluded.profile_pic_url,
                    last_seen_at    = excluded.last_seen_at
                """,
                (
                    author_id,
                    author_username,
                    author_full_name,
                    int(author_is_private),
                    author_pic_url,
                    now,
                    now,
                ),
            )

            if existing:
                # Update existing placeholder/lost → enriched
                conn.execute(
                    """
                    UPDATE posts SET
                        author_id              = ?,
                        author_username_denorm = ?,
                        caption                = ?,
                        media_kind             = ?,
                        taken_at               = ?,
                        state                  = 'enriched',
                        payload_fetched_at     = ?,
                        last_attempted_at      = ?,
                        next_retry_at          = NULL
                    WHERE id = ?
                    """,
                    (
                        author_id,
                        author_username,
                        caption,
                        media_kind,
                        taken_at,
                        now,
                        now,
                        existing["id"],
                    ),
                )
                post_id = existing["id"]
            else:
                # Insert new enriched post
                conn.execute(
                    """
                    INSERT INTO posts(
                        id, shortcode, author_id, author_username_denorm,
                        caption, media_kind, taken_at, saved_at,
                        first_seen_at, last_seen_in_saved_at,
                        is_unsaved, is_source_deleted,
                        state, payload_fetched_at, last_attempted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 0,
                              'enriched', ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        post_id,
                        shortcode,
                        author_id,
                        author_username,
                        caption,
                        media_kind,
                        taken_at,
                        now,
                        now,
                        now,
                        now,
                    ),
                )

            # Upsert raw HTML snippet
            if raw_html_snippet:
                conn.execute(
                    """
                    INSERT INTO posts_raw(post_id, json) VALUES (?, ?)
                    ON CONFLICT(post_id) DO UPDATE SET json = excluded.json
                    """,
                    (post_id, raw_html_snippet),
                )

            # Upsert post_media slides (state='pending').
            # Slides use '' as the media_sha256 placeholder until the actual
            # file is uploaded. The schema requires media_sha256 NOT NULL and
            # FK → media_files(sha256), so we ensure a sentinel row exists.
            if slides:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO media_files(
                        sha256, file_path, mime_type, file_size_bytes, fetched_at
                    ) VALUES ('', '', NULL, 0, '')
                    """
                )

            for slide in slides:
                carousel_index: int = slide.get("carousel_index", 0)
                media_url: str | None = slide.get("media_url")
                media_type: str = slide.get("media_type", "image")

                conn.execute(
                    """
                    INSERT INTO post_media(
                        post_id, media_sha256, thumbnail_sha256,
                        carousel_index, media_type,
                        state, last_url
                    )
                    VALUES (?, '', NULL, ?, ?, 'pending', ?)
                    ON CONFLICT(post_id, carousel_index) DO UPDATE SET
                        media_type = excluded.media_type,
                        last_url   = excluded.last_url,
                        state      = CASE WHEN post_media.state = 'present'
                                         THEN 'present'
                                         ELSE 'pending' END
                    """,
                    (post_id, carousel_index, media_type, media_url),
                )

        else:
            raise HTTPException(
                status_code=400, detail=f"unknown outcome: {outcome!r}"
            )

    return {"ok": True}


# ---------------------------------------------------------------------------
# HEAD /api/ingest/extension/media/exists
# ---------------------------------------------------------------------------


@router.head("/ingest/extension/media/exists", status_code=204)
def media_exists(_: SecretGate, sha: str) -> None:
    """204 if media_files row exists for this sha, else 404."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM media_files WHERE sha256 = ?", (sha,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    # 204 No Content — FastAPI returns empty body for HEAD automatically


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/media
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/media")
async def ingest_media(
    _: SecretGate,
    file: Annotated[UploadFile, File()],
    sha256: Annotated[str, Form()],
    mime: Annotated[str, Form()],
    post_id: Annotated[str, Form()],
    slide_idx: Annotated[int, Form()],
) -> dict[str, Any]:
    """Multipart blob upload. Re-hashes server-side; rejects sha mismatch."""
    # M2: validate post_id (shortcode used as post_id for placeholder rows)
    _validate_shortcode(post_id, field="post_id")
    conn = get_connection()
    try:
        verified_sha = await store_upload(file, sha256, mime, conn=conn)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ShaMismatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Update post_media row: set media_sha256 + state='present'
    with tx_immediate() as wconn:
        wconn.execute(
            """
            UPDATE post_media
            SET media_sha256 = ?,
                state        = 'present',
                last_url     = COALESCE(last_url, last_url)
            WHERE post_id = ? AND carousel_index = ?
            """,
            (verified_sha, post_id, slide_idx),
        )

    return {"ok": True, "sha256": verified_sha}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/media-failed
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/media-failed")
def ingest_media_failed(_: SecretGate, body: dict[str, Any]) -> dict[str, Any]:
    """Signal a slide is unrecoverable; update retry tracking."""
    post_id: str = body["post_id"]
    slide_idx: int = int(body["slide_idx"])
    attempts: int = int(body.get("attempts", 0))
    # M2: validate post_id
    _validate_shortcode(post_id, field="post_id")

    with tx_immediate() as conn:
        conn.execute(
            """
            UPDATE post_media
            SET retry_count = ?,
                state       = 'media_failed',
                last_attempted_at = ?
            WHERE post_id = ? AND carousel_index = ?
            """,
            (attempts, _now_iso(), post_id, slide_idx),
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/heartbeat
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/heartbeat")
def ingest_heartbeat(_: SecretGate, body: dict[str, Any]) -> dict[str, Any]:
    """Update ingest_meta; fire alert on unhealthy states (rate-limited 30min)."""
    state: str = body.get("state", "ok")
    phase: str | None = body.get("phase")
    metrics: dict[str, Any] | None = body.get("metrics")
    now = _now_iso()

    # Determine current last_phase from meta (for precedence logic)
    conn = get_connection()
    meta = _get_meta(conn)

    _ALERT_STATES = {
        "logged_out",
        "throttling_suspected",
        "storage_low",
        "selectors_broken",
        "extraction_failed",
    }

    # Phase precedence: logged_out > storage_low > throttling_suspected > paused > active
    _PRECEDENCE = {
        "logged_out": 5,
        "storage_low": 4,
        "throttling_suspected": 3,
        "paused": 2,
    }

    current_last_phase = meta["last_phase"] if meta else None

    def _maybe_set_phase(updates: dict, new_phase: str) -> None:
        """Only set last_phase if new_phase strictly outranks current."""
        new_prec = _PRECEDENCE.get(new_phase, 1)
        current_prec = _PRECEDENCE.get(current_last_phase or "", 1)
        if new_prec > current_prec:
            updates["last_phase"] = new_phase

    # Build update fields
    updates: dict[str, Any] = {
        "last_heartbeat_at": now,
    }

    # Apply phase from heartbeat (state field maps to phase for alerting purposes)
    if state == "logged_out":
        updates["last_logged_out_at"] = now
        _maybe_set_phase(updates, "logged_out")
    elif state == "throttling_suspected":
        updates["last_throttling_at"] = now
        _maybe_set_phase(updates, "throttling_suspected")
        if metrics:
            updates["last_throttling_metrics_json"] = json.dumps(metrics)
    elif state == "storage_low":
        updates["last_storage_low_at"] = now
        _maybe_set_phase(updates, "storage_low")
    elif phase:
        # For non-alert states, check precedence before overwriting last_phase
        _maybe_set_phase(updates, phase)

    # Fire alert (rate-limited to once per 30min)
    should_alert = state in _ALERT_STATES
    with tx_immediate() as wconn:
        if should_alert and not _rate_limited(wconn):
            telegram.alert(
                f"Instagram logger alert: state={state!r} phase={phase!r}",
                severity="critical",
            )
            updates["last_alert_at"] = now

        # Build SET clause dynamically
        set_parts = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [1]
        wconn.execute(
            f"UPDATE ingest_meta SET {set_parts} WHERE id = ?",  # noqa: S608
            values,
        )

    # Determine response phase (next /state call will reflect this)
    # If last_phase is a pause state, extension should pause
    updated_meta = _get_meta(conn)
    response_phase = (updated_meta["last_phase"] if updated_meta else phase) or "idle"

    return {"ok": True, "phase": response_phase}


# ---------------------------------------------------------------------------
# POST /api/ingest/extension/resume
# ---------------------------------------------------------------------------


@router.post("/ingest/extension/resume")
def ingest_resume(_: SecretGate, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clear storage_low pause so extension can resume (consensus N3 / R7).

    If last_phase was 'storage_low', resets it to 'enrichment' (or the
    appropriate active phase). Extension should immediately re-poll /state.
    """
    with tx_immediate() as conn:
        meta = _get_meta(conn)
        if meta and meta["last_phase"] == "storage_low":
            conn.execute(
                "UPDATE ingest_meta SET last_phase = NULL WHERE id = 1"
            )
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/posts/{post_id}/retry-page  (NOT secret-gated)
# ---------------------------------------------------------------------------


@router.post("/posts/{post_id}/retry-page")
def retry_page(post_id: str) -> dict[str, Any]:
    """Reset a lost/failed post to placeholder + signal priority to extension."""
    # M2: validate post_id path parameter
    _validate_shortcode(post_id, field="post_id")
    now = _now_iso()
    with tx_immediate() as conn:
        result = conn.execute(
            """
            UPDATE posts
            SET state         = 'placeholder',
                retry_count   = 0,
                next_retry_at = ?,
                last_attempted_at = NULL
            WHERE id = ?
            """,
            (now, post_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="post not found")

        conn.execute(
            """
            UPDATE ingest_meta
            SET priority_target_post_id = ?,
                priority_target_reason  = 'manual_retry_page'
            WHERE id = 1
            """,
            (post_id,),
        )
    return {"ok": True, "queued": True}


# ---------------------------------------------------------------------------
# POST /api/posts/{post_id}/retry-media/{slide_idx}  (NOT secret-gated)
# ---------------------------------------------------------------------------


@router.post("/posts/{post_id}/retry-media/{slide_idx}")
def retry_media(post_id: str, slide_idx: int) -> dict[str, Any]:
    """Reset a media_failed slide to pending + signal priority to extension."""
    # M2: validate post_id path parameter
    _validate_shortcode(post_id, field="post_id")
    with tx_immediate() as conn:
        result = conn.execute(
            """
            UPDATE post_media
            SET state       = 'pending',
                retry_count = 0,
                last_attempted_at = NULL
            WHERE post_id = ? AND carousel_index = ?
            """,
            (post_id, slide_idx),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="slide not found")

        conn.execute(
            """
            UPDATE ingest_meta
            SET priority_target_post_id = ?,
                priority_target_reason  = 'manual_retry_media'
            WHERE id = 1
            """,
            (post_id,),
        )
    return {"ok": True, "queued": True}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ensure_unknown_author(conn: sqlite3.Connection, now: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO authors(
            id, username, full_name, is_private,
            profile_pic_url, first_seen_at, last_seen_at
        ) VALUES ('unknown', 'unknown', NULL, 0, NULL, ?, ?)
        """,
        (now, now),
    )
