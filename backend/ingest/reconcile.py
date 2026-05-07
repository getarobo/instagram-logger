"""Full sync ingestion algorithm (plan §4).

`run_once(triggered_by=...)` is the single entry point. It:

1. Inserts a `running` row in `sync_runs`.
2. Enumerates collections via the IG client (real or fake).
3. Per-post: opens a `BEGIN IMMEDIATE` transaction and upserts authors →
   posts → posts_raw → post_collections → post_media + media_files.
4. After enumeration:
   - clears `is_unsaved=0` for every post seen this run (re-saved comes back);
   - if every collection enumerated successfully (`fully_enumerated=True`),
     marks any prior `is_unsaved=0` post that was NOT seen as `is_unsaved=1`,
     then deletes `post_collections` rows whose `last_seen_at < started_at`.
5. Updates the run row to `state='ok'` (or 'auth_required'/'error').
6. Calls `PRAGMA wal_checkpoint(TRUNCATE)` in the `finally` block.

Locked invariants honored (plan §11 + Must-list):
- per-post BEGIN IMMEDIATE
- atomic media writes (delegated to `media.store.fetch_and_store`)
- content-addressed sha256 (idem)
- `post_collections.last_seen_at` updated every full enumeration
- `fully_enumerated` gates the unsaved sweep
- `wal_checkpoint(TRUNCATE)` after every run
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any

from backend.config import settings as _settings
from backend.db import repo
from backend.db.connection import (
    checkpoint_wal_truncate,
    get_connection,
    tx_immediate,
)
from backend.media import store as media_store


def _pace(low: float, high: float) -> None:
    """Jittered sleep between IG-touching operations.

    No-op when running against the fake fixture (so tests + dev stay snappy)
    or when both bounds are <= 0.
    """
    if (_settings.ig_client or "").lower() == "fake":
        return
    lo = max(0.0, low)
    hi = max(lo, high)
    if hi <= 0:
        return
    time.sleep(random.uniform(lo, hi))


def _now_iso() -> str:
    # Microsecond resolution — second-level timestamps collide for back-
    # to-back runs in tests, which breaks the `last_seen_at < run.started_at`
    # sweep semantics in plan §4.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _media_kind(media: Any) -> str:
    mt = getattr(media, "media_type", None)
    if mt == 1:
        return "image"
    if mt == 2:
        return "video"
    if mt == 8:
        return "carousel"
    return "image"


def _slides_for(media: Any) -> list[tuple[str, str, str | None]]:
    """Return [(url, slide_kind, thumbnail_url|None), ...] for this post.

    For carousels we expand every resource. For single image/video we
    yield exactly one tuple. Returns [] when nothing downloadable.
    """
    mt = getattr(media, "media_type", None)
    if mt == 8:
        out: list[tuple[str, str, str | None]] = []
        for res in getattr(media, "resources", None) or []:
            sub = getattr(res, "media_type", 1)
            if sub == 2:
                url = getattr(res, "video_url", None) or getattr(res, "url", None)
                kind = "video"
            else:
                url = getattr(res, "url", None) or getattr(res, "thumbnail_url", None)
                kind = "image"
            if url:
                thumb = getattr(res, "thumbnail_url", None)
                out.append((str(url), kind, str(thumb) if thumb else None))
        return out
    if mt == 2:
        url = getattr(media, "video_url", None)
        return [(str(url), "video", None)] if url else []
    url = getattr(media, "thumbnail_url", None)
    return [(str(url), "image", None)] if url else []


def _author_fields(media: Any) -> dict[str, Any]:
    user = getattr(media, "user", None)
    if user is None:
        return {
            "author_id": "unknown",
            "username": "unknown",
            "full_name": None,
            "is_private": False,
            "profile_pic_url": None,
        }
    return {
        "author_id": str(getattr(user, "pk", "") or getattr(user, "id", "") or "unknown"),
        "username": str(getattr(user, "username", "") or "unknown"),
        "full_name": getattr(user, "full_name", None),
        "is_private": bool(getattr(user, "is_private", False)),
        "profile_pic_url": str(getattr(user, "profile_pic_url", "") or "") or None,
    }


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    return None


def _media_to_dict(client: Any, media: Any) -> dict[str, Any]:
    fn = getattr(client, "media_to_dict", None)
    if callable(fn):
        return fn(media)
    try:
        return json.loads(media.model_dump_json())
    except Exception:
        try:
            return media.dict()
        except Exception:
            return {"_repr": repr(media)}


def run_once(
    *,
    client: Any | None = None,
    triggered_by: str = "manual",
) -> int:
    """Run one full sync. Returns the `sync_runs.id` row written."""
    if client is None:
        from backend.ig_client import get_client

        client = get_client()

    conn = get_connection()
    started_at = _now_iso()
    run_id = repo.insert_sync_run(conn, started_at=started_at, triggered_by=triggered_by)

    fully_enumerated = False
    seen_post_ids: set[str] = set()
    posts_new = 0
    state = "ok"
    errors_json: str | None = None

    cap = _settings.ig_sync_max_new_posts_per_run
    hit_cap = False

    # Detect ChallengeRequired without forcing instagrapi to be importable.
    challenge_classes: tuple[type[BaseException], ...]
    try:
        from instagrapi.exceptions import ChallengeRequired  # type: ignore[import-untyped]

        challenge_classes = (ChallengeRequired,)
    except Exception:
        challenge_classes = ()

    try:
        # --- enumerate collections ---
        named = client.list_collections()
        all_posts_items = client.list_collection_items("All Posts")
        items_by_collection: list[tuple[str, str, list[Any]]] = [
            ("all_posts", "All Posts", all_posts_items)
        ]
        for col in named:
            cid = str(getattr(col, "id", "") or getattr(col, "pk", ""))
            cname = str(getattr(col, "name", ""))
            if not cid or not cname:
                continue
            items_by_collection.append((cid, cname, client.list_collection_items(cname)))

        # If we got here, every collection enumerated successfully.
        fully_enumerated = True

        # --- per-post upserts ---
        for cid, cname, items in items_by_collection:
            if hit_cap:
                break
            with tx_immediate() as tx:
                repo.upsert_collection(
                    tx,
                    collection_id=cid,
                    name=cname,
                    is_all_posts=(cid == "all_posts"),
                    now=started_at,
                )
            for item in items:
                if cap is not None and posts_new >= cap:
                    hit_cap = True
                    break
                post_id = str(getattr(item, "pk", "") or getattr(item, "id", ""))
                shortcode = str(
                    getattr(item, "code", "") or getattr(item, "shortcode", "")
                )
                if not post_id or not shortcode:
                    continue

                seen_post_ids.add(post_id)
                author = _author_fields(item)
                caption_raw = getattr(item, "caption_text", None)
                caption = caption_raw if isinstance(caption_raw, str) else None
                media_kind = _media_kind(item)
                taken_at = _iso_or_none(getattr(item, "taken_at", None))
                raw_json = json.dumps(_media_to_dict(client, item), default=str)
                slides = _slides_for(item)

                # Pre-fetch all slides BEFORE opening the per-post tx so we
                # don't hold the write lock while talking to the network.
                fetched_slides: list[tuple[int, str, str | None, str]] = []
                fetch_failed = False
                for idx, (url, slide_kind, thumb_url) in enumerate(slides):
                    try:
                        sha = media_store.fetch_and_store(conn, url)
                    except Exception as err:
                        print(
                            f"[skip] {shortcode} slide {idx}: media fetch failed: {err!r}",
                            file=sys.stderr,
                        )
                        fetch_failed = True
                        break
                    _pace(
                        _settings.ig_sync_per_media_delay_min,
                        _settings.ig_sync_per_media_delay_max,
                    )
                    thumb_sha: str | None = None
                    if thumb_url:
                        try:
                            thumb_sha = media_store.fetch_and_store(conn, thumb_url)
                        except Exception:
                            thumb_sha = None
                        else:
                            _pace(
                                _settings.ig_sync_per_media_delay_min,
                                _settings.ig_sync_per_media_delay_max,
                            )
                    fetched_slides.append((idx, sha, thumb_sha, slide_kind))
                if fetch_failed:
                    # Drop this post for this collection iteration; don't
                    # persist a half-built record.
                    continue

                is_new = not repo.post_exists(conn, post_id)
                if is_new:
                    posts_new += 1

                with tx_immediate() as tx:
                    repo.upsert_author(
                        tx,
                        author_id=author["author_id"],
                        username=author["username"],
                        full_name=author["full_name"],
                        is_private=author["is_private"],
                        profile_pic_url=author["profile_pic_url"],
                        now=started_at,
                    )
                    repo.upsert_post(
                        tx,
                        post_id=post_id,
                        shortcode=shortcode,
                        author_id=author["author_id"],
                        author_username=author["username"],
                        caption=caption,
                        media_kind=media_kind,
                        taken_at=taken_at,
                        saved_at=None,
                        now=started_at,
                    )
                    repo.upsert_posts_raw(tx, post_id=post_id, raw_json=raw_json)
                    repo.upsert_post_collection(
                        tx,
                        post_id=post_id,
                        collection_id=cid,
                        now=started_at,
                    )
                    for idx, sha, thumb_sha, slide_kind in fetched_slides:
                        repo.upsert_post_media(
                            tx,
                            post_id=post_id,
                            media_sha256=sha,
                            thumbnail_sha256=thumb_sha,
                            carousel_index=idx,
                            media_type=slide_kind,
                        )
                _pace(
                    _settings.ig_sync_per_post_delay_min,
                    _settings.ig_sync_per_post_delay_max,
                )

        # If we tripped the per-run cap, the enumeration is partial — do
        # NOT run the unsaved sweep on a partial view of the saved set.
        if hit_cap:
            fully_enumerated = False

        # --- reconcile flags ---
        # Re-saved posts come back: clear flags for every pid seen this run.
        with tx_immediate() as tx:
            for pid in seen_post_ids:
                repo.clear_post_flags(tx, pid)

        unsaved_now = 0
        if fully_enumerated:
            prior = repo.post_ids_not_unsaved(conn)
            missing = prior - seen_post_ids
            with tx_immediate() as tx:
                for pid in missing:
                    repo.mark_unsaved(tx, pid)
            unsaved_now = len(missing)
            with tx_immediate() as tx:
                repo.delete_stale_post_collections(tx, run_started_at=started_at)

        repo.finalize_sync_run(
            conn,
            run_id=run_id,
            state="ok",
            finished_at=_now_iso(),
            fully_enumerated=fully_enumerated,
            posts_seen=len(seen_post_ids),
            posts_new=posts_new,
            posts_unsaved=unsaved_now,
            errors_json=None,
        )
        return run_id

    except BaseException as err:  # noqa: BLE001 — top-level boundary
        if challenge_classes and isinstance(err, challenge_classes):
            state = "auth_required"
            errors_json = json.dumps({"err": "ChallengeRequired"})
        else:
            state = "error"
            errors_json = json.dumps({"err": repr(err)})
        repo.finalize_sync_run(
            conn,
            run_id=run_id,
            state=state,
            finished_at=_now_iso(),
            fully_enumerated=fully_enumerated,
            posts_seen=len(seen_post_ids),
            posts_new=posts_new,
            posts_unsaved=0,
            errors_json=errors_json,
        )
        # Re-raise on plain exceptions so the CLI exits non-zero; swallow
        # ChallengeRequired so the scheduler can keep ticking later (plan §5).
        if state == "error":
            raise
        return run_id

    finally:
        try:
            checkpoint_wal_truncate()
        except Exception:
            pass


__all__ = ["run_once"]
