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
  kind              TEXT NOT NULL DEFAULT 'text',   -- text | images | document | poll | article
  visibility        TEXT NOT NULL DEFAULT 'PUBLIC', -- PUBLIC | CONNECTIONS
  reshare_disabled  INTEGER NOT NULL DEFAULT 0,
  link_url          TEXT, link_title TEXT, link_description TEXT,   -- article card
  poll_question     TEXT, poll_options TEXT, poll_duration INTEGER, -- poll: JSON list of options, duration in days
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

CREATE TABLE IF NOT EXISTS post_media (         -- images / PDF attached to a post
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  post_id     INTEGER REFERENCES posts(id) ON DELETE CASCADE,  -- NULL until the post is created (orphans cleaned after 24h)
  kind        TEXT NOT NULL CHECK (kind IN ('image','document')),
  name        TEXT NOT NULL,
  mime        TEXT NOT NULL,
  size        INTEGER NOT NULL,
  path        TEXT NOT NULL,                      -- file under data/media/
  alt_text    TEXT NOT NULL DEFAULT '',
  position    INTEGER NOT NULL DEFAULT 0,
  linkedin_urn TEXT,                              -- cached after upload so retries never re-upload
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_post ON post_media(post_id);
