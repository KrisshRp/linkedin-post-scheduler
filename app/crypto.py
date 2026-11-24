"""Token encryption at rest (Fernet = AES-128-CBC + HMAC-SHA256). Key comes from .env, never from the DB."""
from cryptography.fernet import Fernet, InvalidToken

from .config import ConfigError, get_settings


def _fernet() -> Fernet:
    try:
        return Fernet(get_settings().token_key.encode())
    except (ValueError, TypeError) as e:
        raise ConfigError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from e


def encrypt(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as e:
        raise ValueError("stored token cannot be decrypted (key changed?); reconnect LinkedIn") from e
