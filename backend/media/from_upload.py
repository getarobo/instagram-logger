"""Store an uploaded file via the same atomic-write path as media/store.py.

Wraps starlette UploadFile: stream to tmp, re-hash server-side (sha256),
reject on mismatch, fsync, atomic rename, upsert media_files row.

The client claims a sha256; we never trust it — we always re-hash from bytes.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from starlette.datastructures import UploadFile

from backend.config import settings
from backend.db import repo


class ShaMismatchError(ValueError):
    """Raised when the server-computed sha256 differs from the client's claim."""


class UploadTooLargeError(ValueError):
    """Raised when the upload body exceeds _MAX_UPLOAD_BYTES."""


_DEFAULT_CHUNK = 1 << 16  # 64 KiB
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MiB


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ext_for_mime(mime: str | None) -> str:
    """Map MIME type to a file extension."""
    if not mime:
        return ".bin"
    base = mime.split(";", 1)[0].strip().lower()
    _MAP = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    }
    return _MAP.get(base, ".bin")


async def store_upload(
    upload: UploadFile,
    sha256_claimed: str,
    mime: str,
    *,
    media_dir: Path | None = None,
    tmp_dir: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Stream UploadFile to disk, verify sha256, atomically rename.

    Args:
        upload: starlette UploadFile from the multipart form.
        sha256_claimed: hex sha256 as provided by the client (untrusted).
        mime: MIME type string (used for file extension determination).
        media_dir: override for settings.media_dir (used in tests).
        tmp_dir: override for settings.media_tmp_dir (used in tests).
        conn: SQLite connection; if None, a thread-local connection is obtained.

    Returns:
        The server-verified sha256 hex string.

    Raises:
        ShaMismatchError: if server sha256 != sha256_claimed.
    """
    from backend.db.connection import get_connection  # avoid circular import

    media_dir = media_dir or settings.media_dir
    tmp_dir = tmp_dir or settings.media_tmp_dir
    db_conn = conn or get_connection()

    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / f"{uuid.uuid4().hex}.part"
    hasher = hashlib.sha256()
    bytes_written = 0

    try:
        with open(tmp_path, "wb") as fh:
            while True:
                chunk = await upload.read(_DEFAULT_CHUNK)
                if not chunk:
                    break
                hasher.update(chunk)
                fh.write(chunk)
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise UploadTooLargeError(
                        f"upload exceeds {_MAX_UPLOAD_BYTES} bytes"
                    )
            fh.flush()
            os.fsync(fh.fileno())

        sha_server = hasher.hexdigest()

        if sha_server != sha256_claimed.lower():
            tmp_path.unlink(missing_ok=True)
            raise ShaMismatchError(
                f"sha256 mismatch: client claimed {sha256_claimed!r}, "
                f"server computed {sha_server!r}"
            )

        ext = _ext_for_mime(mime)
        shard = sha_server[:2]
        final_dir = media_dir / shard
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{sha_server}{ext}"

        if final_path.exists():
            # Dedup: drop the tmp duplicate; a media_files row already exists.
            tmp_path.unlink(missing_ok=True)
        else:
            os.rename(tmp_path, final_path)

        rel_path = str(final_path.relative_to(settings.data_dir))
        repo.upsert_media_file(
            db_conn,
            sha256=sha_server,
            file_path=rel_path,
            mime_type=mime or None,
            file_size_bytes=bytes_written,
            width=None,
            height=None,
            duration_seconds=None,
            fetched_at=_now_iso(),
        )
        return sha_server

    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
