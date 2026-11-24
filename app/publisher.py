"""One entry point for publishing: real LinkedIn call, or a dry-run fake with optional fault injection (demo + tests)."""
import uuid

from . import linkedin
from .config import get_settings


def publish(token: str, person_sub: str, text: str, attempt: int) -> str:
    """Return the post URN or raise linkedin.PublishError. `attempt` is 1-based."""
    s = get_settings()
    if not s.dry_run:
        return linkedin.create_post(token, person_sub, text)
    if attempt <= s.fault_inject:  # simulate a temporary LinkedIn outage for the first N attempts
        raise linkedin.PublishError(linkedin.TEMPORARY, "LinkedIn 503: simulated outage (FAULT_INJECT)")
    return f"urn:li:share:dryrun-{uuid.uuid4().hex[:12]}"
