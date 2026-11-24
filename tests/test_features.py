import json
import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

from app import config, db, linkedin, worker
from tests.conftest import get_post

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
JPG = b"\xff\xd8\xff" + b"0" * 50
PDF = b"%PDF-1.4 " + b"0" * 50


def client():
    from app.main import app
    c = TestClient(app)
    c.get("/auth/dev-login", follow_redirects=False)
    return c


def future(minutes=5):
    return db.fmt(db.now() + timedelta(minutes=minutes))


def upload(c, data, name="a.png"):
    return c.post(f"/api/media?name={name}", content=data, headers={"content-type": "application/octet-stream"})


def test_upload_validates_type_and_size():
    with client() as c:
        assert upload(c, PNG).status_code == 201
        assert upload(c, b"not an image").status_code == 415
        assert upload(c, b"").status_code == 422
        assert upload(c, JPG + b"0" * (8 * 1024 * 1024)).status_code == 413


def test_post_with_images_pdf_and_attachment_rules():
    with client() as c:
        a, b = upload(c, PNG).json()["id"], upload(c, JPG, "b.jpg").json()["id"]
        r = c.post("/api/posts", json={"content": "pics", "scheduled_at": future(), "media": [{"id": a, "alt": "first"}, {"id": b}]})
        assert r.status_code == 201 and r.json()["kind"] == "images"
        posts = c.get("/api/posts").json()
        assert [m["alt_text"] for m in posts[0]["media"]] == ["first", ""]
        # attachment cannot be reused by a second post
        assert c.post("/api/posts", json={"content": "again", "scheduled_at": future(), "media": [{"id": a}]}).status_code == 422
        # image + pdf mix rejected, pdf alone accepted
        i, d = upload(c, PNG).json()["id"], upload(c, PDF, "x.pdf").json()["id"]
        assert c.post("/api/posts", json={"content": "mix", "scheduled_at": future(), "media": [{"id": i}, {"id": d}]}).status_code == 422
        assert c.post("/api/posts", json={"content": "doc", "scheduled_at": future(), "media": [{"id": d}]}).json()["kind"] == "document"
        # more than 9 images rejected
        ids = [upload(c, PNG).json()["id"] for _ in range(10)]
        assert c.post("/api/posts", json={"content": "many", "scheduled_at": future(), "media": [{"id": x} for x in ids]}).status_code == 422


def test_poll_and_link_validation():
    with client() as c:
        ok = {"question": "Which?", "options": ["A", "B"], "days": 3}
        assert c.post("/api/posts", json={"content": "p", "scheduled_at": future(), "poll": ok}).json()["kind"] == "poll"
        for bad in ({**ok, "options": ["A"]}, {**ok, "options": ["A", "x" * 31]}, {**ok, "days": 2}, {**ok, "question": ""}):
            assert c.post("/api/posts", json={"content": "q", "scheduled_at": future(), "poll": bad}).status_code == 422
        assert c.post("/api/posts", json={"content": "l", "scheduled_at": future(), "link": {"url": "ftp://x"}}).status_code == 422
        assert c.post("/api/posts", json={"content": "l", "scheduled_at": future(), "link": {"url": "https://example.com", "title": "T"}}).json()["kind"] == "article"
        assert c.post("/api/posts", json={"content": "both", "scheduled_at": future(), "poll": ok, "link": {"url": "https://example.com"}}).status_code == 422


def test_dry_run_publish_caches_media_urns_and_cancel_deletes_files():
    with client() as c:
        a = upload(c, PNG).json()["id"]
        pid = c.post("/api/posts", json={"content": "pic", "post_now": True, "media": [{"id": a}]}).json()["id"]
        import time
        for _ in range(40):
            if get_post(pid)["status"] == "published":
                break
            time.sleep(0.1)
        assert get_post(pid)["status"] == "published"
        with db.connect() as conn:
            assert conn.execute("SELECT linkedin_urn FROM post_media WHERE id=?", (a,)).fetchone()[0].startswith("urn:li:image:")
        # scheduled (future) post with a file: cancel removes the file
        b = upload(c, JPG, "b.jpg").json()["id"]
        c.post("/api/posts", json={"content": "later", "scheduled_at": future(), "media": [{"id": b}]})
        with db.connect() as conn:
            path = conn.execute("SELECT path FROM post_media WHERE id=?", (b,)).fetchone()[0]
            post_id = conn.execute("SELECT post_id FROM post_media WHERE id=?", (b,)).fetchone()[0]
        assert c.delete(f"/api/posts/{post_id}").status_code == 200
        import os
        assert not os.path.exists(path)


def test_linkedin_content_blocks(monkeypatch):
    monkeypatch.setattr(linkedin, "_upload", lambda tok, author, item: f"urn:li:{item['kind']}:{item['id']}")
    saved = []
    base = {"kind": "images", "poll_question": None, "poll_options": None, "poll_duration": None, "link_url": None,
            "link_title": None, "link_description": None}
    img = lambda i, alt="": {"id": i, "kind": "image", "name": "p.png", "alt_text": alt, "linkedin_urn": None, "path": "x"}
    one = linkedin.build_content("t", "a", base, [img(1, "alt")], lambda *a: saved.append(a))
    assert one == {"media": {"id": "urn:li:image:1", "altText": "alt"}} and saved == [(1, "urn:li:image:1")]
    multi = linkedin.build_content("t", "a", base, [img(1), img(2)], lambda *a: None)
    assert multi == {"multiImage": {"images": [{"id": "urn:li:image:1"}, {"id": "urn:li:image:2"}]}}
    # already-uploaded media is not uploaded again (retry safety)
    cached = {**img(3), "linkedin_urn": "urn:li:image:old"}
    assert linkedin.build_content("t", "a", base, [cached], lambda *a: None) == {"media": {"id": "urn:li:image:old"}}
    doc = {**base, "kind": "document"}
    d = {"id": 4, "kind": "document", "name": "Report v2.pdf", "alt_text": "", "linkedin_urn": None, "path": "x"}
    assert linkedin.build_content("t", "a", doc, [d], lambda *a: None) == {"media": {"id": "urn:li:document:4", "title": "Report v2"}}
    poll = {**base, "kind": "poll", "poll_question": "Q?", "poll_options": json.dumps(["A", "B"]), "poll_duration": 7}
    assert linkedin.build_content("t", "a", poll, [], None) == {"poll": {"question": "Q?", "options": [{"text": "A"}, {"text": "B"}],
                                                                        "settings": {"duration": "SEVEN_DAYS"}}}
    art = {**base, "kind": "article", "link_url": "https://e.com", "link_title": "", "link_description": "D"}
    assert linkedin.build_content("t", "a", art, [], None) == {"article": {"source": "https://e.com", "title": "https://e.com", "description": "D"}}
    assert linkedin.build_content("t", "a", {**base, "kind": "text"}, [], None) is None


def test_create_post_body_has_visibility_and_content(monkeypatch):
    sent = {}

    class R:
        status_code = 201
        headers = {"x-restli-id": "urn:li:share:9"}

    monkeypatch.setattr(linkedin, "_send", lambda method, url, **kw: (sent.update(kw), R())[1])
    post = {"content": "hi (x)", "visibility": "CONNECTIONS", "reshare_disabled": 1, "kind": "text"}
    assert linkedin.create_post("t", "sub", post, []) == "urn:li:share:9"
    b = sent["json"]
    assert b["visibility"] == "CONNECTIONS" and b["isReshareDisabledByAuthor"] is True and b["commentary"] == r"hi \(x\)" and "content" not in b


def test_stats_endpoint():
    with client() as c:
        c.post("/api/posts", json={"content": "one", "post_now": True})
        import time
        for _ in range(40):
            if c.get("/api/stats").json()["counts"]["published"] == 1:
                break
            time.sleep(0.1)
        s = c.get("/api/stats").json()
        assert s["counts"]["published"] == 1 and s["success_rate"] == 100 and s["first_try_rate"] == 100
        assert len(s["daily"]) == 14 and s["daily"][-1]["published"] == 1 and s["engagement_available"] is False


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, content TEXT, content_hash TEXT, scheduled_at TEXT, "
                 "status TEXT, attempts INTEGER, max_attempts INTEGER, next_attempt_at TEXT, locked_at TEXT, linkedin_post_urn TEXT, "
                 "last_error TEXT, published_at TEXT, created_at TEXT, updated_at TEXT)")
    conn.commit(); conn.close()
    monkeypatch.setenv("DB_PATH", str(old))
    config.get_settings.cache_clear()
    db.init_schema()
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(posts)")}
    assert {"kind", "visibility", "poll_options", "link_url"} <= cols
