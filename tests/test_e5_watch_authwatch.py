"""E5 — Watch + auth-watch + heat-detection R6 + JSONL alerts tests.

Tests:
  1. test_heartbeat_logged_out_writes_alerts_log
  2. test_heartbeat_throttling_suspected_writes_alerts_log
  3. test_heartbeat_metrics_persists_to_ingest_meta
  4. test_heartbeat_logged_out_alert_rate_limited
  5. test_resume_clears_logged_out
  6. test_resume_clears_throttling_suspected
  7. test_resume_does_not_clear_active_phase
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_data_dir: Path, tmp_path: Path, monkeypatch):
    """TestClient with ingest_secret configured, migrations applied, and
    alerts.log redirected to the tmp directory."""
    import importlib as _il

    from backend.config import settings
    from backend.main import app
    from backend.notify import telegram

    original_secret = settings.ingest_secret
    settings.ingest_secret = "test-secret"

    # Redirect alerts.log to tmp_path so tests don't pollute .omc/logs/
    monkeypatch.chdir(tmp_path)
    _il.reload(telegram)

    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        settings.ingest_secret = original_secret
        # Reload again to restore default path for other test modules
        _il.reload(telegram)


SECRET = "test-secret"
HEADERS = {"X-Ingest-Secret": SECRET}


def _alerts_log(tmp_path: Path) -> Path:
    return tmp_path / ".omc" / "logs" / "alerts.log"


# ---------------------------------------------------------------------------
# test_heartbeat_logged_out_writes_alerts_log
# ---------------------------------------------------------------------------


def test_heartbeat_logged_out_writes_alerts_log(
    client: TestClient, tmp_data_dir: Path, tmp_path: Path
) -> None:
    """POST heartbeat state=logged_out writes 1 JSONL line to alerts.log
    with severity=critical and a message containing 'logged_out'."""
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "logged_out", "last_error": "login_form"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    log_file = _alerts_log(tmp_path)
    assert log_file.exists(), "alerts.log was not created"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"

    record = json.loads(lines[0])
    assert record["severity"] in ("warning", "critical"), f"Unexpected severity: {record['severity']}"
    # Message should reference the alert state
    assert "logged_out" in record["message"].lower() or "logged" in record["message"].lower(), (
        f"Message does not mention logged_out: {record['message']!r}"
    )
    assert "ts" in record


# ---------------------------------------------------------------------------
# test_heartbeat_throttling_suspected_writes_alerts_log
# ---------------------------------------------------------------------------


def test_heartbeat_throttling_suspected_writes_alerts_log(
    client: TestClient, tmp_data_dir: Path, tmp_path: Path
) -> None:
    """POST heartbeat state=throttling_suspected writes JSONL alert and
    backend stores the metrics JSON."""
    metrics = {"hydration_p50_ms": 4000, "http_4xx_rate": 0.15, "login_redirects": 1}
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "throttling_suspected", "metrics": metrics},
        headers=HEADERS,
    )
    assert r.status_code == 200

    log_file = _alerts_log(tmp_path)
    assert log_file.exists(), "alerts.log was not created"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"

    record = json.loads(lines[0])
    assert record["severity"] in ("warning", "critical")
    assert "throttling" in record["message"].lower() or "throttling_suspected" in record["message"], (
        f"Message does not mention throttling: {record['message']!r}"
    )


# ---------------------------------------------------------------------------
# test_heartbeat_metrics_persists_to_ingest_meta
# ---------------------------------------------------------------------------


def test_heartbeat_metrics_persists_to_ingest_meta(
    client: TestClient, tmp_data_dir: Path
) -> None:
    """POST heartbeat with state=throttling_suspected + metrics: the metrics
    dict is stored in ingest_meta.last_throttling_metrics_json as valid JSON
    with all 3 expected fields."""
    metrics = {"hydration_p50_ms": 3500, "http_4xx_rate": 0.08, "login_redirects": 2}
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "throttling_suspected", "metrics": metrics},
        headers=HEADERS,
    )
    assert r.status_code == 200

    from backend.db.connection import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT last_throttling_metrics_json FROM ingest_meta WHERE id = 1"
    ).fetchone()
    assert row is not None, "ingest_meta row not found"
    assert row["last_throttling_metrics_json"] is not None, "last_throttling_metrics_json is NULL"

    stored = json.loads(row["last_throttling_metrics_json"])
    assert "hydration_p50_ms" in stored, "hydration_p50_ms missing from stored metrics"
    assert "http_4xx_rate" in stored, "http_4xx_rate missing from stored metrics"
    assert "login_redirects" in stored, "login_redirects missing from stored metrics"
    assert stored["hydration_p50_ms"] == 3500
    assert stored["http_4xx_rate"] == pytest.approx(0.08)
    assert stored["login_redirects"] == 2


# ---------------------------------------------------------------------------
# test_heartbeat_logged_out_alert_rate_limited
# ---------------------------------------------------------------------------


def test_heartbeat_logged_out_alert_rate_limited(
    client: TestClient, tmp_data_dir: Path, tmp_path: Path
) -> None:
    """POST heartbeat logged_out twice quickly → only 1 alert line (30-min rate-limit).
    After manipulating last_alert_at to 31min ago, a third POST writes again."""
    # First POST → alert fires
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "logged_out"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    log_file = _alerts_log(tmp_path)
    assert log_file.exists(), "alerts.log not created after first POST"
    lines_after_first = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines_after_first) == 1, f"Expected 1 line after first POST, got {len(lines_after_first)}"

    # Second POST immediately — rate-limited, no second alert
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "logged_out"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    lines_after_second = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines_after_second) == 1, (
        f"Expected still 1 line after rate-limited second POST, got {len(lines_after_second)}"
    )

    # Manipulate last_alert_at to 31 minutes ago to expire the window
    from backend.db.connection import get_connection

    conn = get_connection()
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat(timespec="seconds")
    conn.execute("UPDATE ingest_meta SET last_alert_at = ? WHERE id = 1", (past,))

    # Third POST — window expired, alert should fire again
    r = client.post(
        "/api/ingest/extension/heartbeat",
        json={"state": "logged_out"},
        headers=HEADERS,
    )
    assert r.status_code == 200

    lines_after_third = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines_after_third) == 2, (
        f"Expected 2 lines after window-expired third POST, got {len(lines_after_third)}"
    )
    record = json.loads(lines_after_third[1])
    assert "ts" in record
    assert record["severity"] in ("warning", "critical")


# ---------------------------------------------------------------------------
# test_resume_clears_logged_out
# ---------------------------------------------------------------------------


def test_resume_clears_logged_out(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /resume when last_phase='logged_out' clears it to NULL."""
    from backend.db.connection import get_connection

    conn = get_connection()
    conn.execute("UPDATE ingest_meta SET last_phase = 'logged_out' WHERE id = 1")

    r = client.post("/api/ingest/extension/resume", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    row = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert row is not None
    assert row["last_phase"] is None, f"Expected last_phase=NULL, got {row['last_phase']!r}"


# ---------------------------------------------------------------------------
# test_resume_clears_throttling_suspected
# ---------------------------------------------------------------------------


def test_resume_clears_throttling_suspected(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /resume when last_phase='throttling_suspected' clears it to NULL."""
    from backend.db.connection import get_connection

    conn = get_connection()
    conn.execute("UPDATE ingest_meta SET last_phase = 'throttling_suspected' WHERE id = 1")

    r = client.post("/api/ingest/extension/resume", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    row = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert row is not None
    assert row["last_phase"] is None, f"Expected last_phase=NULL, got {row['last_phase']!r}"


# ---------------------------------------------------------------------------
# test_resume_does_not_clear_active_phase
# ---------------------------------------------------------------------------


def test_resume_does_not_clear_active_phase(client: TestClient, tmp_data_dir: Path) -> None:
    """POST /resume when last_phase='enrichment' leaves the phase unchanged."""
    from backend.db.connection import get_connection

    conn = get_connection()
    conn.execute("UPDATE ingest_meta SET last_phase = 'enrichment' WHERE id = 1")

    r = client.post("/api/ingest/extension/resume", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    row = conn.execute("SELECT last_phase FROM ingest_meta WHERE id = 1").fetchone()
    assert row is not None
    assert row["last_phase"] == "enrichment", (
        f"Expected last_phase='enrichment' to be unchanged, got {row['last_phase']!r}"
    )
