"""LinkedIn client: OAuth 2.0 authorization-code flow, userinfo, and text-post creation (versioned /rest/posts API)."""
import json
import re
from datetime import timedelta
from pathlib import Path
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


POLL_DURATION = {1: "ONE_DAY", 3: "THREE_DAYS", 7: "SEVEN_DAYS", 14: "FOURTEEN_DAYS"}
REST = "https://api.linkedin.com/rest"


def _headers(token: str, as_json: bool = True) -> dict:
    h = {"Authorization": f"Bearer {token}", "Linkedin-Version": get_settings().api_version, "X-Restli-Protocol-Version": "2.0.0"}
    if as_json:
        h["Content-Type"] = "application/json"
    return h


def _send(method: str, url: str, **kw) -> httpx.Response:
    """One HTTP call; network failures become classified PublishErrors."""
    try:
        return httpx.request(method, url, timeout=TIMEOUT, **kw)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise PublishError(TEMPORARY, f"network error before sending ({type(e).__name__})") from e
    except httpx.HTTPError as e:  # read timeout / reset mid-response: the request MAY have succeeded (ambiguous)
        raise PublishError(TEMPORARY, f"no response from LinkedIn ({type(e).__name__}); outcome unknown") from e


def _upload(token: str, author: str, item: dict) -> str:
    """Register + upload one image or PDF. Returns its LinkedIn URN (urn:li:image:.. / urn:li:document:..)."""
    is_img = item["kind"] == "image"
    path = Path(item["path"])
    if not path.is_file():
        raise PublishError(PERMANENT, f"attachment file is missing on the server: {item['name']}")
    r = _send("POST", f"{REST}/{'images' if is_img else 'documents'}?action=initializeUpload", headers=_headers(token),
              json={"initializeUploadRequest": {"owner": author}})
    if r.status_code not in (200, 201):
        raise classify(r.status_code, r.headers, _err_text(r))
    v = r.json().get("value", {})
    urn, url = v.get("image" if is_img else "document"), v.get("uploadUrl")
    if not urn or not url:
        raise PublishError(PERMANENT, "LinkedIn upload registration returned no URN/uploadUrl")
    up = _send("PUT", url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}, content=path.read_bytes())
    if up.status_code not in (200, 201):
        raise classify(up.status_code, up.headers, _err_text(up))
    return urn


def build_content(token: str, author: str, post: dict, media: list[dict], on_urn) -> dict | None:
    """Translate our post row into the API's `content` block. Uploads media once; the URN is cached via on_urn(media_id, urn)."""
    kind = post["kind"]
    if kind in ("images", "document"):
        for m in media:
            if not m["linkedin_urn"]:
                m["linkedin_urn"] = _upload(token, author, m)
                on_urn(m["id"], m["linkedin_urn"])
    if kind == "images":
        imgs = [{"id": m["linkedin_urn"], **({"altText": m["alt_text"]} if m["alt_text"] else {})} for m in media]
        return {"media": imgs[0]} if len(imgs) == 1 else {"multiImage": {"images": imgs}}
    if kind == "document":
        m = media[0]
        return {"media": {"id": m["linkedin_urn"], "title": Path(m["name"]).stem[:200] or "Document"}}
    if kind == "poll":
        return {"poll": {"question": post["poll_question"], "options": [{"text": o} for o in json.loads(post["poll_options"])],
                         "settings": {"duration": POLL_DURATION[post["poll_duration"]]}}}
    if kind == "article":
        art = {"source": post["link_url"], "title": post["link_title"] or post["link_url"]}
        if post["link_description"]:
            art["description"] = post["link_description"]
        return {"article": art}
    return None


def create_post(token: str, person_sub: str, post: dict, media: list[dict], on_urn=lambda *_: None) -> str:
    """Create the post (text, images, PDF, poll or link card). Returns the post URN. Raises PublishError with a classified kind."""
    author = f"urn:li:person:{person_sub}"
    body = {
        "author": author,
        "commentary": escape_commentary(post["content"]),
        "visibility": post["visibility"],
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": bool(post["reshare_disabled"]),
    }
    content = build_content(token, author, post, media, on_urn)
    if content:
        body["content"] = content
    r = _send("POST", POSTS_URL, json=body, headers=_headers(token))
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
