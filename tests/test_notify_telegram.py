"""Group B — Telegram notify stub tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path


def test_alert_logs_warning(caplog: object, tmp_path: Path, monkeypatch) -> None:
    """alert() must emit a WARNING-level log entry."""
    # Redirect .omc/logs to tmp so no real filesystem side-effects
    monkeypatch.chdir(tmp_path)

    from backend.notify.telegram import alert

    with caplog.at_level(logging.WARNING, logger="backend.notify.telegram"):
        alert("test message")

    assert any("test message" in r.message for r in caplog.records)


def test_alert_appends_jsonl(tmp_path: Path, monkeypatch) -> None:
    """alert() must append a JSONL record with ts, severity, message."""
    import importlib

    from backend.notify import telegram

    monkeypatch.chdir(tmp_path)

    # Re-point the module-level path to the tmp cwd
    importlib.reload(telegram)

    telegram.alert("hello", severity="error")

    log_file = tmp_path / ".omc" / "logs" / "alerts.log"
    assert log_file.exists(), "alerts.log was not created"
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["severity"] == "error"
    assert record["message"] == "hello"
    assert "ts" in record
