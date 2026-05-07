"""Ingest CLI — `python -m backend.ingest [--first-page-only]`.

Plan §10 B1: full enumeration over collections + reconcile. The
`--first-page-only` flag is preserved for the Phase 3 vertical slice
demo path; everything else is in `backend.ingest.reconcile`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from backend.config import settings
from backend.db import repo
from backend.db.connection import get_connection, tx_immediate
from backend.db.migrate import apply_migrations
from backend.ig_client import get_client
from backend.ingest.reconcile import run_once
from backend.media import store as media_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _media_kind(media: Any) -> str:
    mt = getattr(media, "media_type", None)
    if mt == 1:
        return "image"
    if mt == 2:
        return "video"
    if mt == 8:
        return "carousel"
    return "image"


def _slide_url(media: Any) -> tuple[str, str] | None:
    """First-slide-only URL extraction for the slice path."""
    mt = getattr(media, "media_type", None)
    if mt == 8:
        resources = getattr(media, "resources", None) or []
        if not resources:
            return None
        first = resources[0]
        sub_mt = getattr(first, "media_type", 1)
        if sub_mt == 2:
            url = getattr(first, "video_url", None) or getattr(first, "url", None)
            return (str(url), "video") if url else None
        url = getattr(first, "url", None) or getattr(first, "thumbnail_url", None)
        return (str(url), "image") if url else None

    if mt == 2:
        url = getattr(media, "video_url", None)
        return (str(url), "video") if url else None

    url = getattr(media, "thumbnail_url", None)
    return (str(url), "image") if url else None


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


def run_first_page(amount: int = 20) -> int:
    """Phase 3 slice path — first page of "All Posts" only, no collections."""
    apply_migrations()
    conn = get_connection()

    client = get_client()
    if not getattr(client, "has_settings", lambda: True)():
        print(
            "No instagrapi settings found. Run `python -m backend.ig_client.login` first.",
            file=sys.stderr,
        )
        return 0

    if hasattr(client, "collection_medias_by_name"):
        items = client.collection_medias_by_name("All Posts", amount=amount)
    else:
        items = client.list_collection_items("All Posts")[:amount]
    now = _now_iso()
    processed = 0

    for media in items:
        post_id = str(getattr(media, "pk", "") or getattr(media, "id", ""))
        shortcode = str(getattr(media, "code", "") or getattr(media, "shortcode", ""))
        if not post_id or not shortcode:
            continue

        slide = _slide_url(media)
        if slide is None:
            continue
        url, slide_type = slide

        author = _author_fields(media)
        caption_obj = getattr(media, "caption_text", None)
        caption = caption_obj if isinstance(caption_obj, str) else None
        media_kind = _media_kind(media)
        taken_at = _iso_or_none(getattr(media, "taken_at", None))
        raw_json = json.dumps(_media_to_dict(client, media), default=str)

        try:
            sha = media_store.fetch_and_store(conn, url)
        except Exception as err:
            print(f"[skip] {shortcode}: media fetch failed: {err!r}", file=sys.stderr)
            continue

        with tx_immediate() as tx:
            repo.upsert_author(
                tx,
                author_id=author["author_id"],
                username=author["username"],
                full_name=author["full_name"],
                is_private=author["is_private"],
                profile_pic_url=author["profile_pic_url"],
                now=now,
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
                now=now,
            )
            repo.upsert_posts_raw(tx, post_id=post_id, raw_json=raw_json)
            repo.upsert_post_media(
                tx,
                post_id=post_id,
                media_sha256=sha,
                thumbnail_sha256=None,
                carousel_index=0,
                media_type=slide_type,
            )
        processed += 1
        print(f"[ok] {shortcode}  sha={sha[:12]}…")

    print(f"Done. Processed {processed} post(s).")
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="instagram-logger ingest")
    parser.add_argument(
        "--first-page-only",
        action="store_true",
        help="Phase 3 slice: fetch only ~first page of All Posts (no collections).",
    )
    parser.add_argument("--amount", type=int, default=20)
    args = parser.parse_args(argv)

    if args.first_page_only:
        run_first_page(amount=args.amount)
        return 0

    # Default: full B1 sync (collections + reconcile).
    if settings.ig_client.lower() != "fake":
        client = get_client()
        if not getattr(client, "has_settings", lambda: True)():
            print(
                "No instagrapi settings found. Run `python -m backend.ig_client.login` first.",
                file=sys.stderr,
            )
            return 1

    apply_migrations()
    run_id = run_once(triggered_by="manual")
    print(f"sync_run id={run_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
