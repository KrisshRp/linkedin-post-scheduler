"""Attachment upload (images, PDF). Raw-body upload (no multipart dependency): POST /api/media?name=photo.png with the file bytes.

Files live under data/media/. A row has post_id NULL until a post claims it; unclaimed rows are removed after 24 h.
"""
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from . import db
from .config import get_settings
from .routes_auth import current_user

log = logging.getLogger("media")
router = APIRouter(prefix="/api/media")

# (magic bytes, mime, extension, kind, max bytes)
TYPES = [
    (b"\x89PNG\r\n\x1a\n", "image/png", "png", "image", 8 * 1024 * 1024),
    (b"\xff\xd8\xff", "image/jpeg", "jpg", "image", 8 * 1024 * 1024),
    (b"GIF87a", "image/gif", "gif", "image", 8 * 1024 * 1024),
    (b"GIF89a", "image/gif", "gif", "image", 8 * 1024 * 1024),
    (b"%PDF-", "application/pdf", "pdf", "document", 20 * 1024 * 1024),
]
MAX_BODY = 20 * 1024 * 1024


def media_dir() -> Path:
    d = get_settings().db_path.parent / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def delete_files(paths) -> None:
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete %s", p)


def cleanup_orphans() -> int:
    """Remove uploads never attached to a post (older than 24 h)."""
    cutoff = db.fmt(db.now() - timedelta(hours=24))
    with db.connect() as c:
        rows = c.execute("SELECT id, path FROM post_media WHERE post_id IS NULL AND created_at < ?", (cutoff,)).fetchall()
        for r in rows:
            c.execute("DELETE FROM post_media WHERE id=?", (r["id"],))
    delete_files(r["path"] for r in rows)
    return len(rows)


@router.post("", status_code=201)
async def upload(request: Request, name: str = "file", user: dict = Depends(current_user)):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "File too large (images max 8 MB, PDF max 20 MB)")
    data = await request.body()
    if not data:
        raise HTTPException(422, "Empty file")
    for magic, mime, ext, kind, limit in TYPES:
        if data.startswith(magic):
            break
    else:
        raise HTTPException(415, "Unsupported file type. Use JPG, PNG, GIF or PDF.")
    if len(data) > limit:
        raise HTTPException(413, f"File too large (max {limit // 1024 // 1024} MB for this type)")
    path = media_dir() / f"{uuid.uuid4().hex}.{ext}"
    path.write_bytes(data)
    clean = Path(name).name[:120] or f"file.{ext}"
    with db.connect() as c:
        cur = c.execute("INSERT INTO post_media (user_id, kind, name, mime, size, path, created_at) VALUES (?,?,?,?,?,?,?)",
                        (user["id"], kind, clean, mime, len(data), str(path), db.now_s()))
    return {"id": cur.lastrowid, "kind": kind, "name": clean, "size": len(data)}


def _own(c, media_id: int, user_id: int):
    r = c.execute("SELECT * FROM post_media WHERE id=? AND user_id=?", (media_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "File not found")
    return r


@router.get("/{media_id}/file")
def file(media_id: int, user: dict = Depends(current_user)):
    with db.connect() as c:
        r = _own(c, media_id, user["id"])
    if not Path(r["path"]).is_file():
        raise HTTPException(404, "File missing")
    return FileResponse(r["path"], media_type=r["mime"])


@router.delete("/{media_id}")
def remove(media_id: int, user: dict = Depends(current_user)):
    with db.connect() as c:
        r = _own(c, media_id, user["id"])
        if r["post_id"] is not None:
            raise HTTPException(409, "Attachment already belongs to a post")
        c.execute("DELETE FROM post_media WHERE id=?", (media_id,))
    delete_files([r["path"]])
    return {"ok": True}
