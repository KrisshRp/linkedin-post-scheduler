"""FastAPI app: REST API + static dashboard + in-process background worker."""
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, worker
from .config import ROOT, get_settings
from .logs import setup_logging
from .routes_auth import router as auth_router
from .routes_posts import router as posts_router

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()  # fails fast on missing config
    setup_logging()
    db.init_schema()
    stop = threading.Event()
    thread = worker.start(stop)
    log.info("started mode=%s db=%s", s.publish_mode, s.db_path)
    yield
    stop.set()
    thread.join(timeout=10)


app = FastAPI(title="LinkedIn Post Scheduler", lifespan=lifespan)
_s = get_settings()
app.add_middleware(SessionMiddleware, secret_key=_s.session_secret, https_only=_s.cookie_secure,
                   same_site="lax", max_age=7 * 24 * 3600, session_cookie="li_session")
app.include_router(auth_router)
app.include_router(posts_router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/healthz")
def healthz():
    s = get_settings()
    with db.connect() as c:
        due = c.execute("SELECT COUNT(*) n FROM posts WHERE status='scheduled' AND next_attempt_at <= ?", (db.now_s(),)).fetchone()["n"]
    last = worker.state["last_tick"]
    return {"ok": True, "worker_alive": worker.alive(), "last_tick_seconds_ago": round(time.time() - last, 1) if last else None,
            "due_count": due, "publish_mode": s.publish_mode, "dev_login": s.dry_run}
