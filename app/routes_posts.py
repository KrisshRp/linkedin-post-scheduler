"""Posts API: create (schedule), list, events, cancel, retry. Every route is scoped to the logged-in user."""
import hashlib
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db, worker
from .config import get_settings
from .routes_auth import current_user

log = logging.getLogger("posts")
router = APIRouter(prefix="/api/posts")
PAST_GRACE = timedelta(seconds=30)


class NewPost(BaseModel):
    content: str
    scheduled_at: str = ""  # ISO-8601 with timezone (browser sends UTC "Z"); ignored when post_now
    post_now: bool = False  # publish immediately (worker is woken)


def _hash(content: str) -> str:
    return hashlib.sha256(" ".join(content.split()).encode()).hexdigest()


def _own(c, post_id: int, user_id: int):
    r = c.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (post_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Post not found")
    return r


@router.post("", status_code=201)
def create(body: NewPost, user: dict = Depends(current_user)):
    content = body.content.strip()
    if not 1 <= len(content) <= 3000:
        raise HTTPException(422, "Content must be 1-3000 characters")
    if body.post_now:
        when = db.now()
    else:
        try:
            when = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "scheduled_at must be an ISO-8601 date-time")
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone(timezone.utc).replace(microsecond=0)
        if when < db.now() - PAST_GRACE:
            raise HTTPException(422, "Scheduled time is in the past")
    ts, sched = db.now_s(), db.fmt(when)
    try:
        with db.connect() as c:
            cur = c.execute("INSERT INTO posts (user_id, content, content_hash, scheduled_at, next_attempt_at, max_attempts, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (user["id"], content, _hash(content), sched, sched, get_settings().max_attempts, ts, ts))
            pid = cur.lastrowid
            db.add_event(c, pid, "info", "created", f"scheduled for {sched}")
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Duplicate: this exact post is already scheduled for that time")
    log.info("post_id=%s user_id=%s scheduled for %s%s", pid, user["id"], sched, " (post now)" if body.post_now else "")
    if body.post_now:
        worker.wake.set()
    return {"id": pid, "status": "scheduled", "scheduled_at": sched}


@router.get("")
def list_posts(user: dict = Depends(current_user)):
    with db.connect() as c:
        rows = c.execute("SELECT id, content, scheduled_at, status, attempts, max_attempts, next_attempt_at, linkedin_post_urn, "
                         "last_error, published_at, created_at FROM posts WHERE user_id=? ORDER BY id DESC LIMIT 100",
                         (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@router.get("/{post_id}/events")
def events(post_id: int, user: dict = Depends(current_user)):
    with db.connect() as c:
        _own(c, post_id, user["id"])
        rows = c.execute("SELECT at, level, event, detail FROM post_events WHERE post_id=? ORDER BY id", (post_id,)).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{post_id}")
def cancel(post_id: int, user: dict = Depends(current_user)):
    with db.connect() as c:
        _own(c, post_id, user["id"])
        cur = c.execute("DELETE FROM posts WHERE id=? AND status='scheduled' AND attempts=0", (post_id,))
    if not cur.rowcount:
        raise HTTPException(409, "Only a scheduled post that has not been attempted can be cancelled")
    log.info("post_id=%s cancelled", post_id)
    return {"ok": True}


@router.post("/{post_id}/retry")
def retry(post_id: int, user: dict = Depends(current_user)):
    ts = db.now_s()
    with db.connect() as c:
        _own(c, post_id, user["id"])
        cur = c.execute("UPDATE posts SET status='scheduled', attempts=0, next_attempt_at=?, last_error=NULL, updated_at=? "
                        "WHERE id=? AND status='failed' AND linkedin_post_urn IS NULL", (ts, ts, post_id))
        if not cur.rowcount:
            raise HTTPException(409, "Only a failed post can be retried")
        db.add_event(c, post_id, "info", "manual_retry", "user requested retry")
    return {"ok": True}
