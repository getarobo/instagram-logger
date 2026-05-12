"""GET /api/media/:sha256 — full Range / If-None-Match support.

Plan §6 locked behavior:
- ETag: "<sha256>"; 304 on If-None-Match match.
- Accept-Ranges: bytes; 206 + Content-Range on Range; 416 on invalid.
- Cache-Control: public, max-age=31536000, immutable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.db import repo
from backend.db.connection import get_connection

router = APIRouter()

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1 << 16  # 64 KiB


def _stream_range(path: Path, start: int, end_inclusive: int) -> Iterator[bytes]:
    remaining = end_inclusive - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _stream_full(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            yield chunk


@router.get("/media/{sha256}")
def get_media(
    sha256: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    range_header: str | None = Header(default=None, alias="Range"),
) -> Response:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(status_code=400, detail="invalid sha256")

    conn = get_connection()
    row = repo.get_media_file(conn, sha256)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    file_path = Path(row["file_path"])
    if not file_path.is_absolute():
        file_path = settings.data_dir / file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="file missing on disk")

    total = int(row["file_size_bytes"])
    mime = row["mime_type"] or "application/octet-stream"
    etag = f'"{sha256}"'

    common_headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=304, headers=common_headers)

    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if not m:
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{total}"},
            )
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{total}"},
            )
        if start_s == "":
            # Suffix range: last N bytes.
            length = int(end_s)
            if length <= 0:
                return Response(
                    status_code=416,
                    headers={**common_headers, "Content-Range": f"bytes */{total}"},
                )
            start = max(total - length, 0)
            end_inclusive = total - 1
        else:
            start = int(start_s)
            end_inclusive = int(end_s) if end_s != "" else total - 1
        if start >= total or end_inclusive < start:
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{total}"},
            )
        if end_inclusive >= total:
            end_inclusive = total - 1
        slice_len = end_inclusive - start + 1
        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end_inclusive}/{total}",
            "Content-Length": str(slice_len),
        }
        return StreamingResponse(
            _stream_range(file_path, start, end_inclusive),
            status_code=206,
            media_type=mime,
            headers=headers,
        )

    headers = {**common_headers, "Content-Length": str(total)}
    return StreamingResponse(
        _stream_full(file_path),
        status_code=200,
        media_type=mime,
        headers=headers,
    )
