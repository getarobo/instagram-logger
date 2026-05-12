"""Content-addressed media store. Plan §3 Must-6 / §4 contract:

1. open httpx stream → tmp = data/media/.tmp/<uuid>
2. write chunks; track bytes_written
3. if Content-Length present and bytes_written != Content-Length → unlink, raise ShortReadError
4. fsync; sha256 the file on disk
5. final = data/media/<sha[:2]>/<sha>.<ext>; os.rename(tmp, final)
6. media_files row (insert if absent): sha, path, size, mime, dimensions, fetched_at
7. return sha
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from backend.config import settings
from backend.db import repo


class ShortReadError(RuntimeError):
    """Raised when bytes-written != Content-Length on a media download."""


_DEFAULT_CHUNK = 1 << 16  # 64 KiB


def _ext_for(url: str, content_type: str | None) -> str:
    # Prefer URL extension since IG CDN usually has clean .jpg/.mp4/.webp.
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix:
        return suffix
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guess:
            return guess
    return ".bin"


def _shard_dir(sha: str) -> str:
    return sha[:2]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch_and_store(
    conn: sqlite3.Connection,
    url: str,
    *,
    media_dir: Path | None = None,
    tmp_dir: Path | None = None,
    chunk_size: int = _DEFAULT_CHUNK,
    timeout: float = 30.0,
) -> str:
    """Download `url`, persist atomically, upsert `media_files`. Returns sha256."""
    media_dir = media_dir or settings.media_dir
    tmp_dir = tmp_dir or settings.media_tmp_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / f"{uuid.uuid4().hex}.part"
    bytes_written = 0
    content_length: int | None = None
    content_type: str | None = None

    try:
        if url.startswith("file://"):
            # Offline mode (fake IG client): copy the placeholder bytes
            # through the same atomic-rename + sha pipeline. The Content-
            # Length check still applies — we use the source file size.
            src = _file_url_to_path(url)
            content_length = src.stat().st_size
            content_type = (
                mimetypes.guess_type(src.name)[0] or "application/octet-stream"
            )
            with open(src, "rb") as src_fh, open(tmp_path, "wb") as fh:
                while True:
                    chunk = src_fh.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    bytes_written += len(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        else:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                resp.raise_for_status()
                cl_header = resp.headers.get("content-length")
                content_length = int(cl_header) if cl_header is not None else None
                content_type = resp.headers.get("content-type")
                with open(tmp_path, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size):
                        if chunk:
                            fh.write(chunk)
                            bytes_written += len(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())

        if content_length is not None and bytes_written != content_length:
            raise ShortReadError(
                f"short read: wrote {bytes_written} of declared {content_length} bytes"
            )

        sha, size_on_disk = _hash_file(tmp_path)
        ext = _ext_for(url, content_type)
        shard = _shard_dir(sha)
        final_dir = media_dir / shard
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{sha}{ext}"

        if final_path.exists():
            # Already-stored content: drop the tmp duplicate and ensure a row
            # exists. This preserves dedup (same media reposted, same post in
            # multiple collections).
            tmp_path.unlink(missing_ok=True)
        else:
            os.rename(tmp_path, final_path)

        rel_path = str(final_path.relative_to(settings.data_dir))
        repo.upsert_media_file(
            conn,
            sha256=sha,
            file_path=rel_path,
            mime_type=content_type,
            file_size_bytes=size_on_disk,
            width=None,
            height=None,
            duration_seconds=None,
            fetched_at=_now_iso(),
        )
        return sha

    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _file_url_to_path(url: str) -> Path:
    """Translate a `file://` URL into a local path.

    Used by the fake IG client to route placeholder media through the
    same atomic-rename + sha pipeline as real CDN downloads.
    """
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ValueError(f"not a file:// URL: {url!r}")
    return Path(unquote(parsed.path))


def _hash_file(path: Path, chunk_size: int = _DEFAULT_CHUNK) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size
