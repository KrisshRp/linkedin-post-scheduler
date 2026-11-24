from fastapi.testclient import TestClient

from app import db, logs


def client():
    from app.main import app
    return TestClient(app)


def future(minutes=5):
    from datetime import timedelta
    return db.fmt(db.now() + timedelta(minutes=minutes))


def test_requires_login():
    with client() as c:
        assert c.get("/api/posts").status_code == 401


def test_schedule_list_duplicate_cancel_flow():
    with client() as c:
        c.get("/auth/dev-login", follow_redirects=False)
        body = {"content": "Hello LinkedIn", "scheduled_at": future()}
        r = c.post("/api/posts", json=body)
        assert r.status_code == 201
        assert c.post("/api/posts", json=body).status_code == 409  # same content + time
        posts = c.get("/api/posts").json()
        assert len(posts) == 1 and posts[0]["status"] == "scheduled"
        assert c.delete(f"/api/posts/{posts[0]['id']}").status_code == 200
        assert c.get("/api/posts").json() == []


def test_validation():
    with client() as c:
        c.get("/auth/dev-login", follow_redirects=False)
        assert c.post("/api/posts", json={"content": "  ", "scheduled_at": future()}).status_code == 422
        assert c.post("/api/posts", json={"content": "x" * 3001, "scheduled_at": future()}).status_code == 422
        assert c.post("/api/posts", json={"content": "x", "scheduled_at": "2001-01-01T00:00:00Z"}).status_code == 422
        assert c.post("/api/posts", json={"content": "x", "scheduled_at": "nope"}).status_code == 422


def test_tokens_encrypted_at_rest_and_not_in_logs():
    with client() as c:
        c.get("/auth/dev-login", follow_redirects=False)
    with db.connect() as conn:
        blob = bytes(conn.execute("SELECT access_token_enc FROM oauth_tokens").fetchone()[0])
    assert b"dev-token" not in blob and blob.startswith(b"gAAAA")  # Fernet ciphertext
    assert "Bearer [REDACTED]" in logs.redact("Authorization: Bearer abcdefghijklmnop123")
    assert "SECRETVALUE123" not in logs.redact("access_token=SECRETVALUE123")


def test_users_cannot_touch_each_others_posts():
    from tests.conftest import make_post
    ts = db.now_s()
    with db.connect() as conn:
        conn.execute("INSERT INTO users (linkedin_sub, name, created_at) VALUES ('other','O',?)", (ts,))
        other = conn.execute("SELECT id FROM users WHERE linkedin_sub='other'").fetchone()["id"]
    pid = make_post(other, due="2999-01-01T00:00:00Z")
    with client() as c:
        c.get("/auth/dev-login", follow_redirects=False)
        assert c.delete(f"/api/posts/{pid}").status_code == 404
        assert c.get(f"/api/posts/{pid}/events").status_code == 404


def test_dev_login_disabled_in_live_mode(monkeypatch):
    from app import config
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "x")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "y")
    config.get_settings.cache_clear()
    with client() as c:
        assert c.get("/auth/dev-login", follow_redirects=False).status_code == 404


def test_post_now_publishes_without_waiting_for_the_poll():
    with client() as c:
        c.get("/auth/dev-login", follow_redirects=False)
        r = c.post("/api/posts", json={"content": "right now", "post_now": True})
        assert r.status_code == 201
        import time
        for _ in range(40):  # worker thread wakes immediately; poll is 5 s, so this proves the wake-up
            if c.get("/api/posts").json()[0]["status"] == "published":
                break
            time.sleep(0.1)
        assert c.get("/api/posts").json()[0]["status"] == "published"
