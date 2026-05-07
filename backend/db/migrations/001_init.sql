-- Initial schema for instagram-logger. Plan §2 is the canonical reference;
-- this file ships every table/index/trigger described there even if the
-- Phase 3 vertical slice does not exercise all of them yet (FTS5, collections,
-- sync_runs.fully_enumerated, etc). Schema is never edited in place — future
-- changes ship as new NNN_*.sql files.

CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE authors (
  id              TEXT PRIMARY KEY,
  username        TEXT NOT NULL,
  full_name       TEXT,
  is_private      INTEGER NOT NULL DEFAULT 0,
  profile_pic_url TEXT,
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL
);
CREATE INDEX idx_authors_username ON authors(username);

CREATE TABLE posts (
  id                     TEXT PRIMARY KEY,
  shortcode              TEXT NOT NULL UNIQUE,
  author_id              TEXT NOT NULL REFERENCES authors(id),
  author_username_denorm TEXT NOT NULL,
  caption                TEXT,
  media_kind             TEXT NOT NULL,
  taken_at               TEXT,
  saved_at               TEXT,
  first_seen_at          TEXT NOT NULL,
  last_seen_in_saved_at  TEXT NOT NULL,
  is_unsaved             INTEGER NOT NULL DEFAULT 0,
  is_source_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_posts_saved_at  ON posts(COALESCE(saved_at, first_seen_at) DESC, id DESC);
CREATE INDEX idx_posts_last_seen ON posts(last_seen_in_saved_at DESC);
CREATE INDEX idx_posts_author    ON posts(author_id);
CREATE INDEX idx_posts_unsaved   ON posts(is_unsaved);

CREATE TABLE posts_raw (
  post_id TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  json    TEXT NOT NULL
);

CREATE VIRTUAL TABLE posts_fts USING fts5(
  post_id UNINDEXED,
  caption,
  author_username
);

CREATE TRIGGER posts_ai AFTER INSERT ON posts BEGIN
  INSERT INTO posts_fts(post_id, caption, author_username)
  VALUES (new.id, COALESCE(new.caption, ''), new.author_username_denorm);
END;

CREATE TRIGGER posts_ad AFTER DELETE ON posts BEGIN
  DELETE FROM posts_fts WHERE post_id = old.id;
END;

CREATE TRIGGER posts_au AFTER UPDATE OF caption, author_username_denorm ON posts BEGIN
  DELETE FROM posts_fts WHERE post_id = old.id;
  INSERT INTO posts_fts(post_id, caption, author_username)
  VALUES (new.id, COALESCE(new.caption, ''), new.author_username_denorm);
END;

CREATE TABLE media_files (
  sha256           TEXT PRIMARY KEY,
  file_path        TEXT NOT NULL,
  mime_type        TEXT,
  file_size_bytes  INTEGER NOT NULL,
  width            INTEGER,
  height           INTEGER,
  duration_seconds REAL,
  fetched_at       TEXT NOT NULL
);

CREATE TABLE post_media (
  id               INTEGER PRIMARY KEY,
  post_id          TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  media_sha256     TEXT NOT NULL REFERENCES media_files(sha256),
  thumbnail_sha256 TEXT REFERENCES media_files(sha256),
  carousel_index   INTEGER NOT NULL DEFAULT 0,
  media_type       TEXT NOT NULL,
  UNIQUE(post_id, carousel_index)
);
CREATE INDEX idx_post_media_post ON post_media(post_id);

CREATE TABLE collections (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  is_all_posts  INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE post_collections (
  post_id       TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  added_at      TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  PRIMARY KEY (post_id, collection_id)
);
CREATE INDEX idx_pc_collection ON post_collections(collection_id);

CREATE TABLE sync_runs (
  id                INTEGER PRIMARY KEY,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  state             TEXT NOT NULL,
  triggered_by      TEXT NOT NULL CHECK (triggered_by IN ('schedule','manual','resume')),
  fully_enumerated  INTEGER NOT NULL DEFAULT 0,
  posts_seen        INTEGER DEFAULT 0,
  posts_new         INTEGER DEFAULT 0,
  posts_unsaved     INTEGER DEFAULT 0,
  errors_json       TEXT
);
CREATE INDEX idx_sync_runs_started ON sync_runs(started_at DESC);
