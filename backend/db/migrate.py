"""Apply pending SQL migrations from `backend/db/migrations/*.sql`.

Plan §2 contract:
- Files named `NNN_name.sql`; integer prefix is the version.
- Apply any version not in `schema_migrations`, in order, each in its own
  transaction, then insert the row.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from backend.config import settings
from backend.db.connection import get_connection, write_lock

_VERSION_RE = re.compile(r"^(\d+)_.+\.sql$")


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for entry in sorted(migrations_dir.iterdir()):
        if not entry.is_file():
            continue
        m = _VERSION_RE.match(entry.name)
        if not m:
            continue
        out.append((int(m.group(1)), entry))
    out.sort(key=lambda t: t[0])
    return out


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def apply_migrations(migrations_dir: Path | None = None) -> list[int]:
    """Apply pending migrations. Returns the list of versions applied this call."""
    migrations_dir = migrations_dir or settings.migrations_dir
    conn = get_connection()
    applied_now: list[int] = []
    with write_lock():
        already = _applied_versions(conn)
        for version, path in _discover_migrations(migrations_dir):
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            # `executescript` runs as a single autocommit batch in modern
            # sqlite3 (it implicitly COMMITs any pending transaction and
            # releases enclosing savepoints), so we cannot wrap it in
            # BEGIN/COMMIT or SAVEPOINT. The marker insert below is what
            # makes the migration "applied"; on failure the version stays
            # missing and the next boot will retry. CREATE TABLE IF NOT
            # EXISTS / DROP statements in migration files are responsible
            # for being safe to re-run.
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
            applied_now.append(version)
    return applied_now
