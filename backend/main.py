"""FastAPI application entrypoint.

Lifespan startup:
- Enforce the bind rule (`config.assert_bind_allowed`).
- Apply pending migrations from `backend/db/migrations/`.

Routers (slice scope only):
- /api/posts
- /api/media/:sha256
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import collections as collections_router
from backend.api import media as media_router
from backend.api import posts as posts_router
from backend.config import settings
from backend.db.migrate import apply_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.assert_bind_allowed()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    applied = apply_migrations()
    if applied:
        print(f"[migrate] applied versions: {applied}")
    yield


app = FastAPI(title="instagram-logger", lifespan=lifespan)

app.include_router(posts_router.router, prefix="/api")
app.include_router(collections_router.router, prefix="/api")
app.include_router(media_router.router, prefix="/api")
