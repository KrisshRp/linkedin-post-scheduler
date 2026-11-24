"""SQLite helpers. One short-lived connection per use (thread safe), autocommit, WAL so the UI never blocks the worker."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import ROOT, get_settings

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def now_s() -> str:
    return fmt(now())


def parse(s: str) -> datetime:
    return datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc)


@contextmanager
def connect():
    conn = sqlite3.connect(get_settings().db_path, timeout=15, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))


def add_event(conn, post_id: int, level: str, event: str, detail: str = "") -> None:
    conn.execute("INSERT INTO post_events (post_id, at, level, event, detail) VALUES (?,?,?,?,?)",
                 (post_id, now_s(), level, event, detail[:500]))
