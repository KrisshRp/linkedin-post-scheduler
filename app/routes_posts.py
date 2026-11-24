"""Posts API: create (schedule / post now) with optional images, PDF, poll or link card; list, events, cancel, retry.
Every route is scoped to the logged-in user."""
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, worker
from .config import get_settings
from .routes_auth import current_user
from .routes_media import delete_files

log = logging.getLogger("posts")
router = APIRouter(prefix="/api/posts")
PAST_GRACE = timedelta(seconds=30)
MAX_IMAGES = 9


class MediaRef(BaseModel):
    id: int
    alt: str = Field("", max_length=400)


class PollIn(BaseModel):
    question: str
    options: list[str]
    days: int = 3


class LinkIn(BaseModel):
    url: str
    title: str = ""
    description: str = ""


class NewPost(BaseModel):
    content: str
    scheduled_at: str = ""  # ISO-8601 with timezone (browser sends UTC "Z"); ignored when post_now
    post_now: bool = False  # publish immediately (worker is woken)
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    reshare_disabled: bool = False
    media: list[MediaRef] = []
    poll: PollIn | None = None
    link: LinkIn | None = None


def _hash(content: str) -> str:
    return hashlib.sha256(" ".join(content.split()).encode()).hexdigest()


def _own(c, post_id: int, user_id: int):
    r = c.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (post_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Post not found")
    return r


def _when(body: NewPost) -> datetime:
    if body.post_now:
        return db.now()
    try:
        when = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "scheduled_at must be an ISO-8601 date-time")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    if when < db.now() - PAST_GRACE:
        raise HTTPException(422, "Scheduled time is in the past")
    return when


def _validate_attachment(body: NewPost, user_id: int):
    """One attachment type per post (LinkedIn allows one `content` block). Returns (kind, media_rows, poll_opts)."""
    if sum(bool(x) for x in (body.media, body.poll, body.link)) > 1:
        raise HTTPException(422, "Choose only one of: images/PDF, poll, link")
    if body.poll:
        q = body.poll.question.strip()
        opts = [o.strip() for o in body.poll.options if o.strip()]
        if not 1 <= len(q) <= 140:
            raise HTTPException(422, "Poll question must be 1-140 characters")
        if not 2 <= len(opts) <= 4 or any(len(o) > 30 for o in opts):
            raise HTTPException(422, "Poll needs 2-4 options of at most 30 characters")
        if body.poll.days not in (1, 3, 7, 14):
            raise HTTPException(422, "Poll duration must be 1, 3, 7 or 14 days")
        return "poll", [], opts
    if body.link:
        u = urlparse(body.link.url.strip())
        if u.scheme not in ("http", "https") or not u.netloc or len(body.link.url) > 2000:
            raise HTTPException(422, "Link must be a valid http(s) URL")
        return "article", [], []
    if body.media:
        ids = [m.id for m in body.media]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "Duplicate attachment")
        with db.connect() as c:
            rows = c.execute(f"SELECT * FROM post_media WHERE user_id=? AND post_id IS NULL AND id IN ({','.join('?' * len(ids))})",
                             (user_id, *ids)).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(422, "Attachment not found or already used; upload it again")
        kinds = {r["kind"] for r in rows}
        if kinds == {"document"} and len(rows) == 1:
            return "document", rows, []
        if kinds == {"image"} and len(rows) <= MAX_IMAGES:
            return "images", rows, []
        raise HTTPException(422, f"Attach up to {MAX_IMAGES} images, or a single PDF (not both)")
    return "text", [], []


@router.post("", status_code=201)
def create(body: NewPost, user: dict = Depends(current_user)):
    content = body.content.strip()
    if not 1 <= len(content) <= 3000:
        raise HTTPException(422, "Content must be 1-3000 characters")
    when = _when(body)
    kind, media_rows, poll_opts = _validate_attachment(body, user["id"])
    ts, sched = db.now_s(), db.fmt(when)
    link, poll = body.link, body.poll
    alts = {m.id: m.alt.strip() for m in body.media}
    order = {m.id: i for i, m in enumerate(body.media)}
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute(
                "INSERT INTO posts (user_id, content, content_hash, scheduled_at, next_attempt_at, max_attempts, created_at, updated_at, "
                "kind, visibility, reshare_disabled, link_url, link_title, link_description, poll_question, poll_options, poll_duration) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user["id"], content, _hash(content), sched, sched, get_settings().max_attempts, ts, ts, kind, body.visibility,
                 int(body.reshare_disabled), link.url.strip() if link else None, link.title.strip()[:200] if link else None,
                 link.description.strip()[:400] if link else None, poll.question.strip() if poll else None,
                 json.dumps(poll_opts) if poll else None, poll.days if poll else None))
            pid = cur.lastrowid
            for r in media_rows:
                c.execute("UPDATE post_media SET post_id=?, alt_text=?, position=? WHERE id=?", (pid, alts[r["id"]], order[r["id"]], r["id"]))
            db.add_event(c, pid, "info", "created", f"{kind}, scheduled for {sched}")
            c.execute("COMMIT")
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            raise HTTPException(409, "Duplicate: this exact post is already scheduled for that time")
        except Exception:
            c.execute("ROLLBACK")
            raise
    log.info("post_id=%s user_id=%s kind=%s scheduled for %s%s", pid, user["id"], kind, sched, " (post now)" if body.post_now else "")
    if body.post_now:
        worker.wake.set()
    return {"id": pid, "status": "scheduled", "scheduled_at": sched, "kind": kind}


@router.get("")
def list_posts(user: dict = Depends(current_user)):
    with db.connect() as c:
        rows = c.execute("SELECT id, content, scheduled_at, status, attempts, max_attempts, next_attempt_at, linkedin_post_urn, "
                         "last_error, published_at, created_at, kind, visibility, link_url, poll_question, poll_options, poll_duration "
                         "FROM posts WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
        media = c.execute("SELECT id, post_id, kind, name, alt_text FROM post_media WHERE post_id IN "
                          "(SELECT id FROM posts WHERE user_id=? ORDER BY id DESC LIMIT 100) ORDER BY position, id", (user["id"],)).fetchall()
    by_post: dict[int, list] = {}
    for m in media:
        by_post.setdefault(m["post_id"], []).append({"id": m["id"], "kind": m["kind"], "name": m["name"], "alt_text": m["alt_text"]})
    out = []
    for r in rows:
        d = dict(r)
        d["poll_options"] = json.loads(d["poll_options"]) if d["poll_options"] else None
        d["media"] = by_post.get(d["id"], [])
        out.append(d)
    return out


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
        paths = [r["path"] for r in c.execute("SELECT path FROM post_media WHERE post_id=?", (post_id,))]
        cur = c.execute("DELETE FROM posts WHERE id=? AND status='scheduled' AND attempts=0", (post_id,))
    if not cur.rowcount:
        raise HTTPException(409, "Only a scheduled post that has not been attempted can be cancelled")
    delete_files(paths)
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
    worker.wake.set()
    return {"ok": True}
