import threading

import pytest

from app import config, db, linkedin, publisher, worker
from tests.conftest import get_post, make_post


def events(pid):
    with db.connect() as c:
        return [r["event"] for r in c.execute("SELECT event FROM post_events WHERE post_id=? ORDER BY id", (pid,))]


def test_due_post_is_published_once(user):
    pid = make_post(user)
    assert worker.tick() == 1
    p = get_post(pid)
    assert p["status"] == "published" and p["linkedin_post_urn"] and p["attempts"] == 1
    assert worker.tick() == 0  # nothing left; no second publish
    assert events(pid) == ["claimed", "published"]


def test_future_post_is_not_touched(user):
    pid = make_post(user, due="2999-01-01T00:00:00Z")
    assert worker.tick() == 0
    assert get_post(pid)["status"] == "scheduled"


def test_temporary_errors_retry_with_growing_delay_then_publish(user, monkeypatch):
    monkeypatch.setenv("FAULT_INJECT", "2")
    config.get_settings.cache_clear()
    pid = make_post(user)
    seen = []
    for _ in range(3):
        worker.process(worker.claim_due())
        p = get_post(pid)
        seen.append(p)
        if p["status"] == "scheduled":
            assert worker.claim_due() is None  # backoff pushes it into the future
            db_force_due(pid)
    assert [p["status"] for p in seen] == ["scheduled", "scheduled", "published"]
    gap1 = (db.parse(seen[0]["next_attempt_at"]) - db.parse(seen[0]["updated_at"])).total_seconds()
    gap2 = (db.parse(seen[1]["next_attempt_at"]) - db.parse(seen[1]["updated_at"])).total_seconds()
    assert gap2 > gap1 * 2  # increasing delays
    assert seen[2]["linkedin_post_urn"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"] == 1  # no duplicate rows


def db_force_due(pid):
    with db.connect() as c:
        c.execute("UPDATE posts SET next_attempt_at='2000-01-01T00:00:00Z' WHERE id=?", (pid,))


def test_gives_up_after_max_attempts(user, monkeypatch):
    monkeypatch.setenv("FAULT_INJECT", "99")
    config.get_settings.cache_clear()
    pid = make_post(user)
    for _ in range(5):
        worker.process(worker.claim_due())
        db_force_due(pid)
    p = get_post(pid)
    assert p["status"] == "failed" and p["attempts"] == 5 and "gave up" in p["last_error"]
    assert worker.claim_due() is None


def test_permanent_error_fails_immediately(user, monkeypatch):
    def boom(*a, **k):
        raise linkedin.PublishError(linkedin.PERMANENT, "LinkedIn 422: bad content")
    monkeypatch.setattr(publisher, "publish", boom)
    pid = make_post(user)
    worker.tick()
    p = get_post(pid)
    assert p["status"] == "failed" and p["attempts"] == 1 and "422" in p["last_error"]


def test_expired_token_fails_with_reconnect_message(user):
    with db.connect() as c:
        c.execute("UPDATE oauth_tokens SET expires_at='2000-01-01T00:00:00Z'")
    pid = make_post(user)
    worker.tick()
    p = get_post(pid)
    assert p["status"] == "failed" and "Log in again" in p["last_error"]


def test_two_workers_one_row_one_winner(user):
    make_post(user)
    wins = []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        wins.append(worker.claim_due())

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for w in wins if w) == 1


def test_stuck_processing_is_reaped_but_urn_rows_are_not(user):
    stuck = make_post(user, "a", status="processing", locked_at="2000-01-01T00:00:00Z", attempts=1)
    done = make_post(user, "b", status="processing", locked_at="2000-01-01T00:00:00Z", attempts=1,
                     linkedin_post_urn="urn:li:share:1")
    assert worker.reap_stuck() == 1
    assert get_post(stuck)["status"] == "scheduled"
    assert get_post(done)["status"] == "processing"


def test_row_with_urn_is_never_reposted(user):
    pid = make_post(user, linkedin_post_urn="urn:li:share:1")
    assert worker.claim_due() is None
    assert get_post(pid)["attempts"] == 0


def test_classify_http_errors():
    assert linkedin.classify(503, {}, "x").kind == linkedin.TEMPORARY
    assert linkedin.classify(429, {"retry-after": "120"}, "x").retry_after == 120
    assert linkedin.classify(401, {}, "x").kind == linkedin.AUTH
    assert linkedin.classify(422, {}, "x").kind == linkedin.PERMANENT
    assert linkedin.classify(403, {}, "x").kind == linkedin.PERMANENT


def test_commentary_escaping_keeps_hashtags():
    assert linkedin.escape_commentary("a (b) #tag_x") == r"a \(b\) #tag\_x"
