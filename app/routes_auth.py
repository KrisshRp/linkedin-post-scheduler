"""Login with LinkedIn (OAuth 2.0 authorization code) and session helpers."""
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import crypto, db, linkedin
from .config import get_settings

log = logging.getLogger("auth")
router = APIRouter()


def current_user(request: Request) -> dict:
    uid = request.session.get("user_id")
    if uid:
        with db.connect() as c:
            u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            return dict(u)
    raise HTTPException(401, "Not logged in")


def _save_user_and_token(sub: str, name: str, email: str, picture: str, access: str, refresh: str | None,
                         scope: str, expires_at: str) -> int:
    ts = db.now_s()
    with db.connect() as c:
        c.execute("INSERT INTO users (linkedin_sub, name, email, picture, created_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(linkedin_sub) DO UPDATE SET name=excluded.name, email=excluded.email, picture=excluded.picture",
                  (sub, name, email, picture, ts))
        uid = c.execute("SELECT id FROM users WHERE linkedin_sub=?", (sub,)).fetchone()["id"]
        c.execute("INSERT INTO oauth_tokens (user_id, access_token_enc, refresh_token_enc, scope, expires_at, updated_at) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET access_token_enc=excluded.access_token_enc, "
                  "refresh_token_enc=excluded.refresh_token_enc, scope=excluded.scope, expires_at=excluded.expires_at, "
                  "updated_at=excluded.updated_at",
                  (uid, crypto.encrypt(access), crypto.encrypt(refresh) if refresh else None, scope, expires_at, ts))
    log.info("user_id=%s token stored (encrypted), expires %s", uid, expires_at)
    return uid


@router.get("/auth/login")
def login(request: Request):
    s = get_settings()
    if not s.client_id:
        raise HTTPException(500, "LINKEDIN_CLIENT_ID is not configured")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(linkedin.authorize_url(state))


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        raise HTTPException(400, f"LinkedIn login cancelled or refused: {error_description or error}")
    expected = request.session.pop("oauth_state", None)
    if not expected or not secrets.compare_digest(expected, state) or not code:
        raise HTTPException(400, "Invalid OAuth state; start the login again")
    try:
        tok = linkedin.exchange_code(code)
        info = linkedin.userinfo(tok["access_token"])
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    uid = _save_user_and_token(info["sub"], info.get("name", ""), info.get("email", ""), info.get("picture", ""),
                               tok["access_token"], tok.get("refresh_token"), tok.get("scope", ""),
                               linkedin.expires_at(tok.get("expires_in", 5184000)))
    request.session["user_id"] = uid
    return RedirectResponse("/")


@router.get("/auth/dev-login")
def dev_login(request: Request):
    """Offline demo/test login. Only exists when PUBLISH_MODE=dry_run, so it can never post for real."""
    s = get_settings()
    if not s.dry_run:
        raise HTTPException(404, "Not found")
    uid = _save_user_and_token("dev-user", "Dev User", "dev@example.com", "", "dev-token", None, "dry_run",
                               db.fmt(db.now() + timedelta(days=60)))
    request.session["user_id"] = uid
    return RedirectResponse("/")


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/api/me")
def me(request: Request):
    u = current_user(request)
    with db.connect() as c:
        t = c.execute("SELECT expires_at FROM oauth_tokens WHERE user_id=?", (u["id"],)).fetchone()
    left = (db.parse(t["expires_at"]) - db.now()).total_seconds() / 86400 if t else None
    return {"name": u["name"], "email": u["email"], "picture": u["picture"],
            "token_expires_at": t["expires_at"] if t else None,
            "token_days_left": round(left, 1) if left is not None else None}
