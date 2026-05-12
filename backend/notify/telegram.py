"""Telegram alert stub. Real Telegram Bot API wiring deferred to E8.

Per consensus R8 / Δ7: every call ALSO appends a JSONL line to
.omc/logs/alerts.log so that during the E5→E8 window the user has
a persistent record of session-loss events even without Telegram.

TODO(2026-05-12, gated on E8): replace the log line below with a real
Telegram Bot API send. The JSONL append MUST stay regardless — it is the
forensic record between E5 (alerts start firing) and E8 (Telegram is live).
Env vars (future): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
# httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_ALERTS_LOG = Path(".omc/logs/alerts.log")


def alert(message: str, *, severity: str = "warning") -> None:
    """Log an alert and append a JSONL record to .omc/logs/alerts.log."""
    _ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "severity": severity,
        "message": message,
    }
    try:
        with _ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        log.error("Failed to write alerts.log: %s", e)
    log.warning("[TELEGRAM TODO severity=%s] %s", severity, message)
