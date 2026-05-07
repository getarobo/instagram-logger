"""GET /api/collections — plan §6 / §10 B1.

Returns `[{id, name, is_all_posts, post_count, cover_post_id}]`, with
`is_all_posts` collections sorted to the top.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.db import repo
from backend.db.connection import get_connection

router = APIRouter()


@router.get("/collections")
def list_collections() -> dict:
    conn = get_connection()
    items = repo.list_collections(conn)
    return {"items": items}
