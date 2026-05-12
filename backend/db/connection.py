"""SQLite connection management.

Plan §3 / §11 mandates:
- One connection per thread via `threading.local()`.
- A single shared `RLock` serializing writes.
- `check_same_thread=False` so we can hand the connection across the asyncio
  to_thread boundary if needed; the lock keeps writes safe.
- Pragmas (WAL, NORMAL, foreign_keys) applied on every newly minted connection.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.config import settings

_local = threading.local()
_write_lock = threading.RLock()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a thread-local connection, creating it if missing."""
    path = db_path or settings.db_path
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        _local.conn = conn
    return conn


@contextmanager
def tx_immediate() -> Iterator[sqlite3.Connection]:
    """Acquire the write lock and run a `BEGIN IMMEDIATE` transaction.

    Used by ingest to wrap the per-post upsert sequence (plan §4 step Must-7).
    `isolation_level=None` puts us in autocommit mode, so we drive the
    transaction explicitly.
    """
    conn = get_connection()
    with _write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def write_lock() -> threading.RLock:
    """Expose the shared write lock for callers that need it explicitly."""
    return _write_lock


def checkpoint_wal_truncate() -> None:
    conn = get_connection()
    with _write_lock:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def close_thread_connection() -> None:
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
