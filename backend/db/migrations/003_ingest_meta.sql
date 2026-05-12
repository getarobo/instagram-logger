-- Migration 003: ingest_meta singleton table.
-- Stores heartbeat state, alert timestamps, and one-shot priority target.
-- Consensus R6 (heat detection), R7 (storage exhaustion), R8 (alert persistence).

PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE ingest_meta (
  id                           INTEGER PRIMARY KEY CHECK (id = 1),
  last_heartbeat_at            TEXT,
  last_phase                   TEXT,
  last_logged_out_at           TEXT,
  last_throttling_at           TEXT,
  last_throttling_metrics_json TEXT,
  last_storage_low_at          TEXT,
  last_alert_at                TEXT,
  priority_target_post_id      TEXT,
  priority_target_reason       TEXT
);

INSERT INTO ingest_meta(id) VALUES (1);

INSERT INTO schema_migrations(version, applied_at) VALUES (3, datetime('now'));

COMMIT;
