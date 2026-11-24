"""Settings loaded from .env (real environment variables win). Fails fast with a message naming the variable."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_base_url: str
    host: str
    port: int
    log_level: str
    db_path: Path
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    api_version: str
    token_key: str
    session_secret: str
    cookie_secure: bool
    poll_seconds: float
    max_attempts: int
    retry_base: float
    retry_max: float
    publish_mode: str  # live | dry_run
    fault_inject: int

    @property
    def dry_run(self) -> bool:
        return self.publish_mode == "dry_run"


def _need(name: str, hint: str = "") -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise ConfigError(f"Missing {name} in .env. {hint}".strip())
    return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    mode = os.getenv("PUBLISH_MODE", "live").strip().lower()
    if mode not in ("live", "dry_run"):
        raise ConfigError("PUBLISH_MODE must be 'live' or 'dry_run'")
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    live = mode == "live"
    db = Path(os.getenv("DB_PATH", "data/app.db"))
    return Settings(
        app_base_url=base,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        db_path=db if db.is_absolute() else ROOT / db,
        client_id=_need("LINKEDIN_CLIENT_ID", "Get it from the LinkedIn app's Auth tab.") if live else os.getenv("LINKEDIN_CLIENT_ID", ""),
        client_secret=_need("LINKEDIN_CLIENT_SECRET", "Get it from the LinkedIn app's Auth tab.") if live else os.getenv("LINKEDIN_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("LINKEDIN_REDIRECT_URI", f"{base}/auth/callback"),
        scopes=os.getenv("LINKEDIN_SCOPES", "openid profile email w_member_social"),
        api_version=os.getenv("LINKEDIN_API_VERSION", "202511"),
        token_key=_need("TOKEN_ENCRYPTION_KEY", 'Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'),
        session_secret=_need("SESSION_SECRET", 'Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"'),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "5")),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "5")),
        retry_base=float(os.getenv("RETRY_BASE_SECONDS", "30")),
        retry_max=float(os.getenv("RETRY_MAX_SECONDS", "7200")),
        publish_mode=mode,
        fault_inject=int(os.getenv("FAULT_INJECT", "0")),
    )
