-- Database schema (SQLite). Executed at startup by app/db.py (idempotent).
-- All timestamps are UTC, format YYYY-MM-DDTHH:MM:SSZ (fixed width, so string comparison orders correctly).

CREATE TABLE IF NOT EXISTS users (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  linkedin_sub TEXT NOT NULL UNIQUE,          -- OpenID subject; author URN = urn:li:person:<sub>
  name         TEXT NOT NULL DEFAULT '',
  email        TEXT NOT NULL DEFAULT '',
  picture      TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  access_token_enc  BLOB NOT NULL,            -- Fernet ciphertext, never plaintext
  refresh_token_enc BLOB,                     -- usually NULL for self-serve apps
  scope             TEXT NOT NULL DEFAULT '',
  expires_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content           TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 3000),
  content_hash      TEXT NOT NULL,            -- sha256 of whitespace-normalised content
  scheduled_at      TEXT NOT NULL,            -- user-chosen publish time
  status            TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled','processing','published','failed')),
  attempts          INTEGER NOT NULL DEFAULT 0,
  max_attempts      INTEGER NOT NULL DEFAULT 5,
  next_attempt_at   TEXT NOT NULL,            -- = scheduled_at at first, pushed out on retry
  locked_at         TEXT,                     -- set when the worker claims the row
  linkedin_post_urn TEXT UNIQUE,              -- set in the same commit that marks it published
  last_error        TEXT,
  published_at      TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE (user_id, content_hash, scheduled_at)  -- same content + time cannot be created twice
);
CREATE INDEX IF NOT EXISTS idx_posts_due ON posts(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS post_events (        -- per-post audit trail shown in the UI
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  at      TEXT NOT NULL,
  level   TEXT NOT NULL,                        -- info | warn | error
  event   TEXT NOT NULL,                        -- created, claimed, attempt, retry_scheduled, published, failed, reaped
  detail  TEXT NOT NULL DEFAULT ''              -- sanitised; never tokens
);
CREATE INDEX IF NOT EXISTS idx_events_post ON post_events(post_id, id);
