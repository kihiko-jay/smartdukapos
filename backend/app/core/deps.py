"""
FastAPI dependency injectors — v4.0

Changes vs v2.4 (v3):
  - PLATFORM_OWNER role added — bypasses store_id scoping on all endpoints
  - require_premium updated to allow PLATFORM_OWNER through unconditionally
  - require_platform_owner dependency added for platform-only admin endpoints
  - All other behaviour unchanged
"""

import time
import logging
from collections import defaultdict
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.core.security import decode_token
from app.models.employee import Employee, Role
from app.models.subscription import Store, SubStatus
from app.core.config import settings

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── DB session ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── In-process fallback rate limiter ─────────────────────────────────────────

class _InProcessSlidingWindowLimiter:
    def __init__(self, max_calls: int, window_seconds: int = 60):
        self._max    = max_calls
        self._window = window_seconds
        self._store: dict[str, list] = defaultdict(list)
        self._lock   = Lock()

    def is_allowed(self, key: str) -> bool:
        now    = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            self._store[key] = [t for t in self._store[key] if t > cutoff]
            if len(self._store[key]) >= self._max:
                return False
            self._store[key].append(now)
            return True


# ── Redis-backed rate limiter ─────────────────────────────────────────────────

class _RedisSlidingWindowLimiter:
    _LUA_SCRIPT = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then return 0 end
redis.call('ZADD', key, now, now .. ':' .. math.random(1,1000000))
redis.call('PEXPIRE', key, window)
return 1
"""

    def __init__(self, max_calls: int, window_ms: int = 60_000):
        self._max      = max_calls
        self._window   = window_ms
        self._client   = None
        self._script   = None
        self._fallback = _InProcessSlidingWindowLimiter(max_calls, window_ms // 1000)

    def init(self, redis_client) -> None:
        self._client = redis_client
        try:
            self._script = self._client.register_script(self._LUA_SCRIPT)
            logger.info("Redis rate limiter initialised (max=%d/min)", self._max)
        except Exception as exc:
            logger.warning("Redis rate limiter script registration failed: %s", exc)
            self._client = None

    def is_allowed(self, key: str) -> bool:
        if self._client is None:
            return self._fallback.is_allowed(key)
        try:
            now_ms = int(time.time() * 1000)
            result = self._script(keys=[key], args=[now_ms, self._window, self._max])
            return bool(result)
        except Exception as exc:
            logger.debug("Redis rate limiter fallback for key=%s: %s", key, exc)
            return self._fallback.is_allowed(key)


_login_limiter = _RedisSlidingWindowLimiter(
    max_calls=settings.RATE_LIMIT_LOGIN_PER_MINUTE,
    window_ms=60_000,
)

_api_limiter = _RedisSlidingWindowLimiter(
    max_calls=settings.RATE_LIMIT_API_PER_MINUTE,
    window_ms=60_000,
)


def init_rate_limiters(redis_client=None) -> None:
    if redis_client is None:
        logger.info("Rate limiters using in-process fallback (no Redis)")
        return
    _login_limiter.init(redis_client)
    _api_limiter.init(redis_client)


def login_rate_limiter(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    key = f"rate:login:{client_ip}"
    if not _login_limiter.is_allowed(key):
        logger.warning("Rate limit hit on /auth/login from %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait a minute and try again.",
            headers={"Retry-After": "60"},
        )


def api_rate_limiter(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    key = f"rate:api:{client_ip}"
    if not _api_limiter.is_allowed(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please slow down.",
            headers={"Retry-After": "60"},
        )


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_current_employee(
    token: str = Depends(oauth2_scheme),
    db:    Session = Depends(get_db),
) -> Employee:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if not payload:
        raise exc

    try:
        employee_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise exc

    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp or not emp.is_active:
        raise exc

    return emp


def require_role(*roles: Role):
    def _check(current: Employee = Depends(get_current_employee)) -> Employee:
        if current.role not in roles:
            logger.warning(
                "Access denied for employee %s (role=%s, required=%s)",
                current.id, current.role, [r.value for r in roles],
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}",
            )
        return current
    return _check


# PLATFORM_OWNER is included in every role group so they are never locked out
require_cashier    = require_role(Role.CASHIER, Role.SUPERVISOR, Role.MANAGER, Role.ADMIN, Role.PLATFORM_OWNER)
require_supervisor = require_role(Role.SUPERVISOR, Role.MANAGER, Role.ADMIN, Role.PLATFORM_OWNER)
require_manager    = require_role(Role.MANAGER, Role.ADMIN, Role.PLATFORM_OWNER)
require_admin      = require_role(Role.ADMIN, Role.PLATFORM_OWNER)

# NEW: platform-only endpoints — shop admins cannot reach these
require_platform_owner = require_role(Role.PLATFORM_OWNER)


# ── Subscription gate ─────────────────────────────────────────────────────────

def require_premium(
    current: Employee = Depends(get_current_employee),
    db:      Session  = Depends(get_db),
) -> Employee:
    """
    Blocks access unless the store has an active paid plan or valid trial.

    PLATFORM_OWNER bypasses this check — they need access to all stores
    for support and debugging without needing a subscription themselves.
    """
    # Platform owner bypasses subscription check
    if current.role == Role.PLATFORM_OWNER:
        return current

    if not current.store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code":    "PREMIUM_REQUIRED",
                "message": "This feature requires a paid plan.",
                "plans":   _plan_details(),
            },
        )

    store = db.query(Store).filter(Store.id == current.store_id).first()

    if not store or not store.is_premium:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code":         "PREMIUM_REQUIRED",
                "message":      "Your free trial has ended or you are on the Free plan.",
                "current_plan": store.plan if store else "free",
                "plans":        _plan_details(),
            },
        )
    return current


def _plan_details():
    return [
        {"plan": "starter", "price_kes": 1500, "period": "month",
         "features": ["1 store", "Full back office", "Inventory", "Reports", "Employees", "KRA eTIMS"]},
        {"plan": "growth",  "price_kes": 3500, "period": "month",
         "features": ["Up to 3 stores", "Everything in Starter", "Multi-store reports", "Priority support"]},
        {"plan": "pro",     "price_kes": 7500, "period": "month",
         "features": ["Unlimited stores", "Everything in Growth", "API access", "Dedicated support"]},
    ]
