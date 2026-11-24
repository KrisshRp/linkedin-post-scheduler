"""Dashboard statistics computed from our own data. (LinkedIn engagement metrics need the restricted r_member_social scope,
which self-serve apps cannot get: socialActions / memberCreatorPostAnalytics return 403.)"""
from datetime import timedelta

from fastapi import APIRouter, Depends

from . import db
from .routes_auth import current_user

router = APIRouter(prefix="/api/stats")
DAYS = 14


@router.get("")
def stats(user: dict = Depends(current_user)):
    uid = user["id"]
    with db.connect() as c:
        counts = {s: 0 for s in ("scheduled", "processing", "published", "failed")}
        for r in c.execute("SELECT status, COUNT(*) n FROM posts WHERE user_id=? GROUP BY status", (uid,)):
            counts[r["status"]] = r["n"]
        done = counts["published"] + counts["failed"]
        first_try = c.execute("SELECT COUNT(*) n FROM posts WHERE user_id=? AND status='published' AND attempts=1", (uid,)).fetchone()["n"]
        avg_att = c.execute("SELECT AVG(attempts) a FROM posts WHERE user_id=? AND status IN ('published','failed')", (uid,)).fetchone()["a"]
        delay = c.execute("SELECT AVG((julianday(published_at) - julianday(scheduled_at)) * 86400) d FROM posts "
                          "WHERE user_id=? AND status='published'", (uid,)).fetchone()["d"]
        today = db.now().date()
        start = today - timedelta(days=DAYS - 1)
        series = {(start + timedelta(days=i)).isoformat(): {"published": 0, "failed": 0} for i in range(DAYS)}
        for r in c.execute("SELECT date(published_at) d, COUNT(*) n FROM posts WHERE user_id=? AND status='published' AND date(published_at)>=? "
                           "GROUP BY d", (uid, start.isoformat())):
            series[r["d"]]["published"] = r["n"]
        for r in c.execute("SELECT date(updated_at) d, COUNT(*) n FROM posts WHERE user_id=? AND status='failed' AND date(updated_at)>=? "
                           "GROUP BY d", (uid, start.isoformat())):
            series[r["d"]]["failed"] = r["n"]
        reasons = c.execute("SELECT substr(last_error,1,80) reason, COUNT(*) n FROM posts WHERE user_id=? AND status='failed' "
                            "AND last_error IS NOT NULL GROUP BY reason ORDER BY n DESC LIMIT 5", (uid,)).fetchall()
    return {
        "counts": counts,
        "success_rate": round(100 * counts["published"] / done) if done else None,
        "first_try_rate": round(100 * first_try / counts["published"]) if counts["published"] else None,
        "avg_attempts": round(avg_att, 1) if avg_att else None,
        "avg_publish_delay_seconds": round(delay) if delay is not None else None,
        "daily": [{"date": d, **v} for d, v in series.items()],
        "failure_reasons": [dict(r) for r in reasons],
        "engagement_available": False,
    }
