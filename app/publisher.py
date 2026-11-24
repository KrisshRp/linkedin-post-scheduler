"""One entry point for publishing: real LinkedIn call, or a dry-run fake with optional fault injection (demo + tests)."""
import uuid

from . import linkedin
from .config import get_settings


def publish(token: str, person_sub: str, post: dict, media: list[dict], attempt: int, on_urn=lambda *_: None) -> str:
    """Return the post URN or raise linkedin.PublishError. `attempt` is 1-based."""
    s = get_settings()
    if not s.dry_run:
        return linkedin.create_post(token, person_sub, post, media, on_urn)
    if attempt <= s.fault_inject:  # simulate a temporary LinkedIn outage for the first N attempts
        raise linkedin.PublishError(linkedin.TEMPORARY, "LinkedIn 503: simulated outage (FAULT_INJECT)")
    for m in media:  # pretend uploads (cached so a retry would not re-upload)
        if not m["linkedin_urn"]:
            m["linkedin_urn"] = f"urn:li:{m['kind']}:dryrun-{uuid.uuid4().hex[:10]}"
            on_urn(m["id"], m["linkedin_urn"])
    return f"urn:li:share:dryrun-{uuid.uuid4().hex[:12]}"
