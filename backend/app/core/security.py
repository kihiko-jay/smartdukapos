"""
Security utilities: password hashing, JWT access + refresh tokens.

Token architecture:
  - Access token:  short-lived (configurable, default 30 min)
  - Refresh token: long-lived (configurable, default 8 h / one shift)
  - Both are JWTs signed with the same SECRET_KEY but carry a `type` claim
    so a refresh token CANNOT be used as an access token and vice-versa.
  - Token payload always includes `jti` (JWT ID) for future revocation support.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import time

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_ACCESS_TOKEN_TYPE  = "access"
_REFRESH_TOKEN_TYPE = "refresh"


# ── Refresh-token revocation blocklist ────────────────────────────────────────
# Stores revoked jti values until their natural expiry.
# Dict: jti (str) -> expiry epoch (float).
# In a multi-process / multi-worker deployment swap this for a Redis SET with
# TTL — the interface (revoke_token / is_token_revoked) stays identical.
_REVOKED_JTIS: dict[str, float] = {}


def _prune_revoked() -> None:
    """Remove expired entries (lazy GC — called on each write)."""
    now = time.time()
    expired = [j for j, exp in _REVOKED_JTIS.items() if exp < now]
    for j in expired:
        _REVOKED_JTIS.pop(j, None)


def revoke_token(payload: dict) -> None:
    """
    Revoke a token by its jti. Safe to call with access or refresh payloads.
    The entry is kept until the token's own exp so it can never be replayed.
    """
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return
    _prune_revoked()
    _REVOKED_JTIS[jti] = float(exp)


def is_token_revoked(payload: dict) -> bool:
    """Return True if this token's jti has been explicitly revoked."""
    jti = payload.get("jti")
    if not jti:
        return False
    entry = _REVOKED_JTIS.get(jti)
    if entry is None:
        return False
    # Expired entries are no longer relevant (token already invalid by exp)
    if entry < time.time():
        _REVOKED_JTIS.pop(jti, None)
        return False
    return True


# ── Password utils ────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── Token creation ────────────────────────────────────────────────────────────

def _make_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    payload = data.copy()
    now = datetime.now(timezone.utc)
    payload.update({
        "type": token_type,
        "iat":  now,
        "exp":  now + expires_delta,
        "jti":  str(uuid.uuid4()),   # unique token ID — enables revocation
    })
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _make_token(data, _ACCESS_TOKEN_TYPE, delta)


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    delta = expires_delta or timedelta(hours=settings.REFRESH_TOKEN_EXPIRE_HOURS)
    return _make_token(data, _REFRESH_TOKEN_TYPE, delta)


# ── Token decoding (strict) ───────────────────────────────────────────────────

def _decode_strict(token: str, expected_type: str) -> Optional[dict]:
    """
    Decode and validate a JWT.

    Strict checks:
      1. Signature must be valid
      2. Token must not be expired
      3. `type` claim must match expected_type (prevents refresh-as-access attacks)
      4. `sub` claim must be present and non-empty
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type", "jti"]},
        )
    except JWTError:
        return None

    if payload.get("type") != expected_type:
        return None
    if not payload.get("sub"):
        return None
    if is_token_revoked(payload):
        return None

    return payload


def decode_token(token: str) -> Optional[dict]:
    """Decode an access token. Returns None on any validation failure."""
    return _decode_strict(token, _ACCESS_TOKEN_TYPE)


def decode_refresh_token(token: str) -> Optional[dict]:
    """Decode a refresh token. Returns None on any validation failure."""
    return _decode_strict(token, _REFRESH_TOKEN_TYPE)
