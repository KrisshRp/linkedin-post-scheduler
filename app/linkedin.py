"""LinkedIn client: OAuth 2.0 authorization-code flow, userinfo, and text-post creation (versioned /rest/posts API)."""
import re
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from . import db
from .config import get_settings

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# error kinds drive the worker: temporary -> retry with backoff; permanent/auth -> failed
TEMPORARY, PERMANENT, AUTH = "temporary", "permanent", "auth"


class PublishError(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


def authorize_url(state: str) -> str:
    s = get_settings()
    return AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": s.client_id, "redirect_uri": s.redirect_uri,
                                       "state": state, "scope": s.scopes})


def exchange_code(code: str) -> dict:
    """code -> {access_token, expires_in, scope, refresh_token?}. Raises RuntimeError with a safe message."""
    s = get_settings()
    r = httpx.post(TOKEN_URL, timeout=TIMEOUT, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": s.redirect_uri,
        "client_id": s.client_id, "client_secret": s.client_secret})
    if r.status_code != 200:
        raise RuntimeError(f"LinkedIn token exchange failed ({r.status_code}): {_err_text(r)}")
    return r.json()


def userinfo(access_token: str) -> dict:
    r = httpx.get(USERINFO_URL, timeout=TIMEOUT, headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        raise RuntimeError(f"LinkedIn userinfo failed ({r.status_code}): {_err_text(r)}")
    return r.json()


def expires_at(expires_in: int | float) -> str:
    return db.fmt(db.now() + timedelta(seconds=int(expires_in)))


_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def escape_commentary(text: str) -> str:
    """The posts API parses 'little text' markup; reserved characters must be backslash-escaped. '#' stays so hashtags work."""
    return _RESERVED.sub(r"\\\1", text)


def create_post(token: str, person_sub: str, text: str) -> str:
    """Create a public text post. Returns the post URN. Raises PublishError with a classified kind."""
    s = get_settings()
    body = {
        "author": f"urn:li:person:{person_sub}",
        "commentary": escape_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    headers = {"Authorization": f"Bearer {token}", "Linkedin-Version": s.api_version,
               "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    try:
        r = httpx.post(POSTS_URL, json=body, headers=headers, timeout=TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise PublishError(TEMPORARY, f"network error before sending ({type(e).__name__})") from e
    except httpx.HTTPError as e:  # read timeout / reset mid-response: the post MAY exist (ambiguous)
        raise PublishError(TEMPORARY, f"no response from LinkedIn ({type(e).__name__}); outcome unknown") from e
    if r.status_code == 201:
        urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id")
        if not urn:
            raise PublishError(PERMANENT, "LinkedIn returned 201 without a post id; not retrying to avoid a duplicate")
        return urn
    raise classify(r.status_code, r.headers, _err_text(r))


def classify(status: int, headers, message: str) -> PublishError:
    msg = f"LinkedIn {status}: {message}"
    if status == 429:
        ra = headers.get("retry-after")
        return PublishError(TEMPORARY, msg, float(ra) if ra and ra.isdigit() else None)
    if status >= 500:
        return PublishError(TEMPORARY, msg)
    if status == 426:
        return PublishError(PERMANENT, f"{msg}. Set LINKEDIN_API_VERSION in .env to a recent YYYYMM.")
    if status == 401:
        return PublishError(AUTH, "LinkedIn rejected the token (expired or revoked). Log in again to reconnect.")
    return PublishError(PERMANENT, msg)  # 400, 403, 404, 409, 422 ...


def _err_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        return str(j.get("message") or j.get("error_description") or j.get("error") or j)[:300]
    except ValueError:
        return r.text[:300]
