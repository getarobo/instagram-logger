"""GET /api/posts — B1 version.

- No filter           -> 60 most-recently-saved across all collections.
- `?collection_id=X`  -> same query joined to `post_collections`.

No cursor yet (plan §10 B3 lands cursor + search).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.db import repo
from backend.db.connection import get_connection

router = APIRouter()


@router.get("/posts")
def list_posts(collection_id: str | None = Query(default=None)) -> dict:
    conn = get_connection()
    items = repo.list_recent_posts(conn, limit=60, collection_id=collection_id)
    return {"items": items, "next_cursor": None}


@router.get("/posts/{post_id}")
def get_post(post_id: str) -> dict:
    conn = get_connection()
    detail = repo.get_post_detail(conn, post_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="post not found")
    return detail
