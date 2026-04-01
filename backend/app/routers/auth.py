"""
Auth router.

Changes vs original:
  - /login now returns access_token + refresh_token (short + long lived)
  - /token/refresh endpoint added — swaps a valid refresh token for a new access token
  - login_rate_limiter dependency applied to /login to prevent brute force
  - /login no longer returns 401 with distinct messages for "no user" vs "bad password"
    (both return the same message to prevent user enumeration)
  - All DB writes are inside explicit transactions
"""

import re
import uuid as _uuid
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_employee, require_admin, require_premium, login_rate_limiter
from app.core.security import (
    verify_password, hash_password,
    create_access_token, create_refresh_token, decode_refresh_token,
    revoke_token,
)
from app.models.employee import Employee
from app.schemas.auth import LoginRequest, TokenOut, EmployeeCreate, EmployeeOut, PinSet, PinVerify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

# ── WS Ticket store ───────────────────────────────────────────────────────────
# Short-lived (30 s) one-time tickets so WebSocket auth never puts a JWT in
# the query string (which leaks into access logs, proxies, and browser history).
# Dict: ticket_id -> {"employee_id": int, "expires": float}
# Cleaned up lazily on each issuance.
_WS_TICKETS: dict = {}
_WS_TICKET_TTL = 30  # seconds


def _issue_ws_ticket(employee_id: int) -> str:
    # Lazy expiry cleanup
    now = time.monotonic()
    expired = [k for k, v in _WS_TICKETS.items() if v["expires"] < now]
    for k in expired:
        _WS_TICKETS.pop(k, None)

    ticket = str(_uuid.uuid4())
    _WS_TICKETS[ticket] = {"employee_id": employee_id, "expires": now + _WS_TICKET_TTL}
    return ticket


def consume_ws_ticket(ticket: str) -> int | None:
    """Validate and consume a one-time WS ticket. Returns employee_id or None."""
    entry = _WS_TICKETS.pop(ticket, None)
    if entry is None:
        return None
    if entry["expires"] < time.monotonic():
        return None
    return entry["employee_id"]


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    _rate: None = Depends(login_rate_limiter),   # ← rate limit: 10/min per IP
):
    employee = db.query(Employee).filter(Employee.email == payload.email).first()

    # Constant-time: always call verify_password even if employee not found
    # This prevents timing-based user enumeration.
    dummy_hash = "$2b$12$KIX/9f3sWWZD3zMgXeF0DOCKTiOGl3YC5Dy4a8ZlqG5v5tQXiKrpy"
    password_ok = verify_password(payload.password, employee.password if employee else dummy_hash)

    if not employee or not password_ok:
        logger.warning("Failed login attempt for email=%s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not employee.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    token_data = {"sub": str(employee.id), "role": employee.role.value}
    access_token  = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    logger.info("Employee %s logged in (role=%s)", employee.id, employee.role)

    return TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        employee_id=employee.id,
        full_name=employee.full_name,
        role=employee.role,
        terminal_id=employee.terminal_id,
    )


# ── WS Ticket ─────────────────────────────────────────────────────────────────

@router.post("/ws-ticket")
def issue_ws_ticket(current: Employee = Depends(get_current_employee)):
    """
    Issue a short-lived (30 s) one-time ticket for WebSocket authentication.

    The Electron POS client calls this immediately before opening the WS
    connection, then passes ?ticket=<uuid> instead of ?token=<jwt>.
    This prevents the JWT from appearing in nginx access logs, browser
    history, or any reverse-proxy request log.

    The ticket is consumed on first use and expires automatically after 30 s.
    """
    ticket = _issue_ws_ticket(current.id)
    return {"ticket": ticket, "expires_in": _WS_TICKET_TTL}


# ── Token refresh ─────────────────────────────────────────────────────────────

@router.post("/token/refresh")
def refresh_token(body: dict, db: Session = Depends(get_db)):
    """
    Exchange a valid refresh token for a new access token.

    The refresh token is validated strictly:
      - must be signed correctly
      - must not be expired
      - `type` claim must equal "refresh"
      - employee must still exist and be active

    Returns a new access token (and a new refresh token to support token rotation).
    """
    token = body.get("refresh_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    payload = decode_refresh_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    try:
        employee_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Malformed token")

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee or not employee.is_active:
        raise HTTPException(status_code=401, detail="Employee not found or deactivated")

    token_data = {"sub": str(employee.id), "role": employee.role.value}

    # Revoke the old refresh token before issuing a new pair (token rotation)
    revoke_token(payload)

    return {
        "access_token":  create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type":    "bearer",
    }


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(body: dict, current: Employee = Depends(get_current_employee)):
    """
    Revoke the supplied refresh token immediately.

    The client should send its current refresh_token in the request body.
    The access token expires naturally (≤30 min). The refresh token is
    blocklisted server-side so it cannot be used to mint new access tokens
    even before its exp claim is reached.
    """
    token = body.get("refresh_token", "")
    if token:
        payload = decode_refresh_token(token)
        if payload:
            revoke_token(payload)
    return {"message": "Logged out successfully"}


# ── Employee management ───────────────────────────────────────────────────────

@router.post("/employees", response_model=EmployeeOut, dependencies=[Depends(require_premium)])
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)):
    if db.query(Employee).filter(Employee.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    emp = Employee(
        **payload.model_dump(exclude={"password"}),
        password=hash_password(payload.password),
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


# ── Clock in / out ────────────────────────────────────────────────────────────

@router.post("/clock-in")
def clock_in(
    db: Session = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    current.clocked_in_at  = datetime.now(timezone.utc)
    current.clocked_out_at = None
    db.commit()
    return {"message": f"Clocked in at {current.clocked_in_at.strftime('%H:%M')}"}


@router.post("/clock-out")
def clock_out(
    db: Session = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    current.clocked_out_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": f"Clocked out at {current.clocked_out_at.strftime('%H:%M')}"}


# ── PIN management ────────────────────────────────────────────────────────────

@router.post("/set-pin")
def set_pin(
    payload: PinSet,
    db: Session = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Set the current employee's quick-access PIN (4–8 digits)."""
    if not re.fullmatch(r"\d{4,8}", payload.pin):
        raise HTTPException(status_code=422, detail="PIN must be 4–8 numeric digits only")
    current.pin = hash_password(payload.pin)
    db.commit()
    return {"message": "PIN updated"}


@router.post("/verify-pin")
def verify_pin(
    payload: PinVerify,
    db: Session = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Verify the current employee's PIN (POS quick-lock screen)."""
    if current.pin is None:
        return {"valid": False, "reason": "PIN not set"}
    return {"valid": verify_password(payload.pin, current.pin)}


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=EmployeeOut)
def me(current: Employee = Depends(get_current_employee)):
    return current
