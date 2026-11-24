"""Background worker + scheduler. The posts table is the queue: a post is due when status='scheduled' AND next_attempt_at <= now.

Same pattern as Job-automation's fetch/gen workers: daemon thread, `stop` Event, DB as queue, never let the loop die.
"""
import logging
import random
import threading
import time
from datetime import timedelta

from . import crypto, db, linkedin, publisher
from .config import get_settings

log = logging.getLogger("worker")
STUCK_AFTER_SECONDS = 600
state = {"last_tick": 0.0, "thread": None}


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Delay before the next try after `attempt` (1-based) failed: base*5^(n-1), capped, +0-20% jitter, honours Retry-After."""
    s = get_settings()
    delay = min(s.retry_base * (5 ** (attempt - 1)), s.retry_max)
    delay *= 1 + random.random() * 0.2
    if retry_after:
        delay = max(delay, retry_after)
    return delay


def reap_stuck(now_dt=None) -> int:
    """A row left in 'processing' (crash/kill) goes back to 'scheduled' (or 'failed' when out of attempts). Never touches rows with a URN."""
    now_dt = now_dt or db.now()
    cutoff = db.fmt(now_dt - timedelta(seconds=STUCK_AFTER_SECONDS))
    n = 0
    with db.connect() as c:
        rows = c.execute("SELECT id, attempts, max_attempts FROM posts WHERE status='processing' AND linkedin_post_urn IS NULL "
                         "AND locked_at <= ?", (cutoff,)).fetchall()
        for r in rows:
            ts = db.fmt(now_dt)
            if r["attempts"] >= r["max_attempts"]:
                new, msg = "failed", "worker interrupted and attempts exhausted"
            else:
                new, msg = "scheduled", "worker interrupted; put back in the queue"
            cur = c.execute("UPDATE posts SET status=?, locked_at=NULL, last_error=?, next_attempt_at=?, updated_at=? "
                            "WHERE id=? AND status='processing'", (new, msg, ts, ts, r["id"]))
            if cur.rowcount:
                db.add_event(c, r["id"], "warn", "reaped", msg)
                log.warning("post_id=%s reaped -> %s", r["id"], new)
                n += 1
    return n


def claim_due(now_s: str | None = None):
    """Atomically flip ONE due row scheduled -> processing. Only one caller can ever win a given row."""
    ts = now_s or db.now_s()
    with db.connect() as c:
        row = c.execute(
            "UPDATE posts SET status='processing', locked_at=:ts, attempts=attempts+1, updated_at=:ts "
            "WHERE id = (SELECT id FROM posts WHERE status='scheduled' AND next_attempt_at <= :ts "
            "            AND linkedin_post_urn IS NULL ORDER BY next_attempt_at, id LIMIT 1) "
            "AND status='scheduled' RETURNING *", {"ts": ts}).fetchone()
        if row:
            db.add_event(c, row["id"], "info", "claimed", f"attempt {row['attempts']}/{row['max_attempts']}")
        return dict(row) if row else None


def _finish(post_id: int, sql: str, params: tuple, level: str, event: str, detail: str) -> None:
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(sql, params)
        db.add_event(c, post_id, level, event, detail)
        c.execute("COMMIT")


def process(post: dict) -> str:
    """Publish one claimed post. Returns the final status of this attempt: published | scheduled (retry) | failed."""
    pid, attempt, ts = post["id"], post["attempts"], db.now_s()
    log.info("post_id=%s user_id=%s attempt %s/%s publishing", pid, post["user_id"], attempt, post["max_attempts"])
    try:
        with db.connect() as c:
            t = c.execute("SELECT t.access_token_enc, t.expires_at, u.linkedin_sub FROM oauth_tokens t "
                          "JOIN users u ON u.id = t.user_id WHERE t.user_id=?", (post["user_id"],)).fetchone()
        if not t:
            raise linkedin.PublishError(linkedin.AUTH, "No LinkedIn connection for this user. Log in again.")
        if t["expires_at"] <= ts:
            raise linkedin.PublishError(linkedin.AUTH, "LinkedIn token expired. Log in again to reconnect.")
        token = crypto.decrypt(t["access_token_enc"])
        urn = publisher.publish(token, t["linkedin_sub"], post["content"], attempt)
    except linkedin.PublishError as e:
        return _handle_error(post, e)
    except ValueError as e:  # undecryptable token
        return _handle_error(post, linkedin.PublishError(linkedin.AUTH, str(e)))
    except Exception as e:  # unknown bug: do not retry blindly (could duplicate)
        log.exception("post_id=%s unexpected error", pid)
        return _handle_error(post, linkedin.PublishError(linkedin.PERMANENT, f"internal error: {type(e).__name__}"))
    # URN is stored in the same commit that marks the post published, so a crash cannot lose it.
    _finish(pid, "UPDATE posts SET status='published', linkedin_post_urn=?, published_at=?, last_error=NULL, locked_at=NULL, updated_at=? WHERE id=?",
            (urn, ts, ts, pid), "info", "published", f"urn={urn}")
    log.info("post_id=%s published urn=%s", pid, urn)
    return "published"


def _handle_error(post: dict, e: linkedin.PublishError) -> str:
    pid, attempt, ts = post["id"], post["attempts"], db.now_s()
    if e.kind == linkedin.TEMPORARY and attempt < post["max_attempts"]:
        delay = backoff_seconds(attempt, e.retry_after)
        nxt = db.fmt(db.now() + timedelta(seconds=delay))
        _finish(pid, "UPDATE posts SET status='scheduled', next_attempt_at=?, last_error=?, locked_at=NULL, updated_at=? WHERE id=?",
                (nxt, e.message, ts, pid), "warn", "retry_scheduled", f"{e.message} | retry in {delay:.0f}s at {nxt}")
        log.warning("post_id=%s temporary error, retry in %.0fs: %s", pid, delay, e.message)
        return "scheduled"
    why = e.message if e.kind != linkedin.TEMPORARY else f"gave up after {attempt} attempts: {e.message}"
    _finish(pid, "UPDATE posts SET status='failed', last_error=?, locked_at=NULL, updated_at=? WHERE id=?",
            (why, ts, pid), "error", "failed", why)
    log.error("post_id=%s failed (%s): %s", pid, e.kind, why)
    return "failed"


def tick() -> int:
    """One scheduler pass: reap stuck rows, then drain every due post one at a time. Returns how many were processed."""
    state["last_tick"] = time.time()
    reap_stuck()
    n = 0
    while (post := claim_due()) is not None:
        process(post)
        n += 1
    return n


def run(stop: threading.Event) -> None:
    log.info("worker started (poll %.0fs)", get_settings().poll_seconds)
    while not stop.is_set():
        try:
            tick()
        except Exception:  # never let the worker die
            log.exception("worker tick error")
        stop.wait(get_settings().poll_seconds)
    log.info("worker stopped")


def start(stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=run, args=(stop,), name="post-worker", daemon=True)
    t.start()
    state["thread"] = t
    return t


def alive() -> bool:
    t = state["thread"]
    return bool(t and t.is_alive())
