-- Migration 002: extension state machine columns + triggers.
-- Wrapped in explicit transaction per consensus Δ1 (atomicity).
-- SQLite ALTER TABLE … ADD COLUMN … CHECK enforces on future writes only;
-- existing rows accept the DEFAULT which is in the allowed set.
-- Verified against SQLite 3.51.0 on dev.

PRAGMA foreign_keys = ON;
BEGIN;

-- posts: recency_rank (0 = newest, captured at discovery time)
ALTER TABLE posts ADD COLUMN recency_rank INTEGER;

-- posts: state machine
ALTER TABLE posts ADD COLUMN state TEXT NOT NULL DEFAULT 'placeholder'
  CHECK (state IN ('placeholder', 'enriched', 'lost'));

-- posts: retry tracking
ALTER TABLE posts ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN next_retry_at TEXT;
ALTER TABLE posts ADD COLUMN last_attempted_at TEXT;
ALTER TABLE posts ADD COLUMN payload_fetched_at TEXT;

-- posts: materialized slide aggregates (consensus Δ3 option b).
-- Maintained by triggers on post_media below; replaces correlated subqueries
-- in /api/posts list endpoint. Mirrors FTS trigger pattern in 001_init.sql.
ALTER TABLE posts ADD COLUMN slides_total   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN slides_present INTEGER NOT NULL DEFAULT 0;
ALTER TABLE posts ADD COLUMN slides_failed  INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_posts_state      ON posts(state);
CREATE INDEX idx_posts_recency    ON posts(recency_rank DESC);
CREATE INDEX idx_posts_next_retry ON posts(next_retry_at)
  WHERE state IN ('placeholder', 'lost') AND next_retry_at IS NOT NULL;

-- post_media: state machine + retry tracking + last_url for re-visit
ALTER TABLE post_media ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'
  CHECK (state IN ('pending', 'present', 'media_failed'));
ALTER TABLE post_media ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE post_media ADD COLUMN revisit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE post_media ADD COLUMN last_url TEXT;
ALTER TABLE post_media ADD COLUMN last_attempted_at TEXT;

CREATE INDEX idx_post_media_state ON post_media(state);

-- Triggers: maintain posts.slides_{total,present,failed} on post_media writes.
-- Mirrors the FTS trigger pattern already used in 001_init.sql.

CREATE TRIGGER post_media_aggr_ins AFTER INSERT ON post_media
BEGIN
  UPDATE posts SET
    slides_total   = slides_total   + 1,
    slides_present = slides_present + (NEW.state = 'present'),
    slides_failed  = slides_failed  + (NEW.state = 'media_failed')
  WHERE id = NEW.post_id;
END;

CREATE TRIGGER post_media_aggr_upd AFTER UPDATE OF state ON post_media
BEGIN
  UPDATE posts SET
    slides_present = slides_present + (NEW.state = 'present')      - (OLD.state = 'present'),
    slides_failed  = slides_failed  + (NEW.state = 'media_failed') - (OLD.state = 'media_failed')
  WHERE id = NEW.post_id;
END;

CREATE TRIGGER post_media_aggr_del AFTER DELETE ON post_media
BEGIN
  UPDATE posts SET
    slides_total   = slides_total   - 1,
    slides_present = slides_present - (OLD.state = 'present'),
    slides_failed  = slides_failed  - (OLD.state = 'media_failed')
  WHERE id = OLD.post_id;
END;

-- Note: legacy columns posts.is_unsaved, posts.is_source_deleted,
-- posts.last_seen_in_saved_at, post_collections.last_seen_at remain nullable
-- but are no longer maintained. Application code never reads or writes them.
-- A future migration can DROP them once we are sure nothing depends on them.

INSERT INTO schema_migrations(version, applied_at) VALUES (2, datetime('now'));

COMMIT;
