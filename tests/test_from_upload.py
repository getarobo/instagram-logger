"""Group C — media/from_upload.py tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_upload(data: bytes, filename: str = "test.jpg") -> MagicMock:
    """Build a minimal async UploadFile mock."""
    chunks = [data, b""]  # one data chunk then EOF
    upload = MagicMock()
    upload.filename = filename

    # read() is an async function that pops from chunks
    async def _read(n: int = -1) -> bytes:
        return chunks.pop(0) if chunks else b""

    upload.read = _read
    return upload


@pytest.mark.asyncio
async def test_store_upload_happy_path(tmp_data_dir: Path) -> None:
    """store_upload with correct sha → file persisted at expected path, sha returned."""
    from backend.db.connection import get_connection
    from backend.media.from_upload import store_upload

    data = b"fake image bytes"
    sha = hashlib.sha256(data).hexdigest()
    mime = "image/jpeg"

    media_dir = tmp_data_dir / "media"
    tmp_dir = media_dir / ".tmp"

    conn = get_connection()
    upload = _make_upload(data)
    result = await store_upload(
        upload, sha, mime, media_dir=media_dir, tmp_dir=tmp_dir, conn=conn
    )

    assert result == sha

    shard = sha[:2]
    expected = media_dir / shard / f"{sha}.jpg"
    assert expected.exists(), f"file not found at {expected}"

    row = conn.execute(
        "SELECT sha256, file_path FROM media_files WHERE sha256 = ?", (sha,)
    ).fetchone()
    assert row is not None
    assert row["sha256"] == sha


@pytest.mark.asyncio
async def test_store_upload_sha_mismatch_raises(tmp_data_dir: Path) -> None:
    """store_upload with wrong sha → ShaMismatchError; no file persisted."""
    from backend.db.connection import get_connection
    from backend.media.from_upload import ShaMismatchError, store_upload

    data = b"some content"
    wrong_sha = "a" * 64
    mime = "image/png"

    media_dir = tmp_data_dir / "media"
    tmp_dir = media_dir / ".tmp"
    conn = get_connection()

    upload = _make_upload(data)
    with pytest.raises(ShaMismatchError):
        await store_upload(upload, wrong_sha, mime, media_dir=media_dir, tmp_dir=tmp_dir, conn=conn)

    # No file should exist under the wrong sha path
    shard = wrong_sha[:2]
    expected = media_dir / shard / f"{wrong_sha}.png"
    assert not expected.exists()

    # No tmp files should survive
    tmp_files = list(tmp_dir.glob("*.part")) if tmp_dir.exists() else []
    assert len(tmp_files) == 0


@pytest.mark.asyncio
async def test_store_upload_idempotent(tmp_data_dir: Path) -> None:
    """Calling store_upload twice with the same blob must not raise; DB row present."""
    from backend.db.connection import get_connection
    from backend.media.from_upload import store_upload

    data = b"idempotent test data"
    sha = hashlib.sha256(data).hexdigest()
    mime = "image/webp"

    media_dir = tmp_data_dir / "media"
    tmp_dir = media_dir / ".tmp"
    conn = get_connection()

    await store_upload(_make_upload(data), sha, mime, media_dir=media_dir, tmp_dir=tmp_dir, conn=conn)
    # Second call with fresh upload mock — should not raise
    await store_upload(_make_upload(data), sha, mime, media_dir=media_dir, tmp_dir=tmp_dir, conn=conn)

    row = conn.execute(
        "SELECT sha256 FROM media_files WHERE sha256 = ?", (sha,)
    ).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_store_upload_atomic_write(tmp_data_dir: Path) -> None:
    """Tmp .part file must not survive after success or after sha mismatch."""
    from backend.db.connection import get_connection
    from backend.media.from_upload import ShaMismatchError, store_upload

    media_dir = tmp_data_dir / "media"
    tmp_dir = media_dir / ".tmp"
    conn = get_connection()

    # Success case
    data = b"atomic write test"
    sha = hashlib.sha256(data).hexdigest()
    await store_upload(
        _make_upload(data), sha, "image/jpeg",
        media_dir=media_dir, tmp_dir=tmp_dir, conn=conn
    )
    surviving = list(tmp_dir.glob("*.part")) if tmp_dir.exists() else []
    assert surviving == [], f"tmp files survived success: {surviving}"

    # Mismatch case
    with pytest.raises(ShaMismatchError):
        await store_upload(
            _make_upload(b"other data"), "b" * 64, "image/jpeg",
            media_dir=media_dir, tmp_dir=tmp_dir, conn=conn
        )
    surviving = list(tmp_dir.glob("*.part")) if tmp_dir.exists() else []
    assert surviving == [], f"tmp files survived mismatch: {surviving}"
