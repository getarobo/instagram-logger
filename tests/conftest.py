"""Pytest harness — every test gets a fresh tmp data dir + sqlite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Point the global `settings` at a tmp directory + reset the connection.

    `settings` is a module-level pydantic instance, so the test mutates its
    fields in place rather than reloading every module that captured it.
    """
    from backend.config import settings
    from backend.db import connection as db_connection
    from backend.db.migrate import apply_migrations

    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    original_data_dir = settings.data_dir
    original_ig_client = settings.ig_client

    settings.data_dir = data
    settings.ig_client = "fake"

    # Drop any thread-local connection from a previous test so the next
    # `get_connection()` opens against the new path.
    db_connection.close_thread_connection()

    apply_migrations()
    try:
        yield data
    finally:
        db_connection.close_thread_connection()
        settings.data_dir = original_data_dir
        settings.ig_client = original_ig_client
