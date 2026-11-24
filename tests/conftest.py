import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Fresh DB + dry_run settings for every test. Fast retry timings."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("PUBLISH_MODE", "dry_run")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("FAULT_INJECT", "0")
    monkeypatch.setenv("RETRY_BASE_SECONDS", "30")
    monkeypatch.setenv("RETRY_MAX_SECONDS", "7200")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore the developer's real .env
    from app import config
    config.get_settings.cache_clear()
    from app import db
    db.init_schema()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def user():
    from app import crypto, db
    ts = db.now_s()
    with db.connect() as c:
        c.execute("INSERT INTO users (linkedin_sub, name, created_at) VALUES ('sub1','T',?)", (ts,))
        uid = c.execute("SELECT id FROM users").fetchone()["id"]
        c.execute("INSERT INTO oauth_tokens (user_id, access_token_enc, scope, expires_at, updated_at) VALUES (?,?,?,?,?)",
                  (uid, crypto.encrypt("SECRET-TOKEN-123456"), "x", "2999-01-01T00:00:00Z", ts))
    return uid


def make_post(uid, content="hello", due="2000-01-01T00:00:00Z", **cols):
    from app import db
    ts = db.now_s()
    with db.connect() as c:
        cur = c.execute("INSERT INTO posts (user_id, content, content_hash, scheduled_at, next_attempt_at, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?)", (uid, content, content, due, due, ts, ts))
        pid = cur.lastrowid
        for k, v in cols.items():
            c.execute(f"UPDATE posts SET {k}=? WHERE id=?", (v, pid))
    return pid


def get_post(pid):
    from app import db
    with db.connect() as c:
        return dict(c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone())
