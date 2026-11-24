"""Logging: console + rotating file, with a filter that redacts anything that looks like a secret."""
import logging
import re
from logging.handlers import RotatingFileHandler

from .config import ROOT, get_settings

_SECRET = re.compile(r"(?i)(bearer\s+|access_token['\"=:\s]+|refresh_token['\"=:\s]+|client_secret['\"=:\s]+)[A-Za-z0-9._\-~+/=]{8,}")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + "[REDACTED]", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging() -> None:
    s = get_settings()
    root = logging.getLogger()
    if getattr(root, "_li_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    (ROOT / "logs").mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler(ROOT / "logs" / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        root.addHandler(h)
    root.setLevel(s.log_level)
    root._li_configured = True  # type: ignore[attr-defined]
