"""
Security tests — PIN hashing, PIN endpoints, sync key enforcement.

These tests cover the three highest-risk changes in Phase 1:
  1. Cashier PINs are now bcrypt-hashed (not plaintext)
  2. /auth/set-pin and /auth/verify-pin work correctly
  3. Sync endpoints reject requests when no API key is set
"""

import pytest
from app.core.security import hash_password, verify_password


# ── 1. Core bcrypt primitives ─────────────────────────────────────────────────

def test_password_hashing():
    hashed = hash_password("abc")
    assert hashed.startswith("$2b$"), "Expected bcrypt hash prefix $2b$"
    assert verify_password("abc", hashed) is True
    assert verify_password("wrong", hashed) is False


# ── 2. PIN set and verify flow ────────────────────────────────────────────────

def test_pin_set_and_verify(client, auth_headers, db, test_admin):
    # Set a PIN
    resp = client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "1234"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "PIN updated"

    # Confirm the stored value is NOT the plaintext PIN
    db.refresh(test_admin)
    assert test_admin.pin != "1234", "PIN must not be stored as plaintext"
    assert test_admin.pin.startswith("$2b$"), "PIN must be bcrypt-hashed"

    # Correct PIN verifies
    resp = client.post(
        "/api/v1/auth/verify-pin",
        json={"pin": "1234"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    # Wrong PIN is rejected
    resp = client.post(
        "/api/v1/auth/verify-pin",
        json={"pin": "9999"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


def test_verify_pin_when_not_set(client, auth_headers, db, test_admin):
    # Ensure PIN is unset
    test_admin.pin = None
    db.commit()

    resp = client.post(
        "/api/v1/auth/verify-pin",
        json={"pin": "1234"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data.get("reason") == "PIN not set"


# ── 3. PIN validation ─────────────────────────────────────────────────────────

def test_pin_must_be_numeric(client, auth_headers):
    resp = client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "abcd"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, "Non-numeric PIN must be rejected with 422"


def test_pin_too_short(client, auth_headers):
    resp = client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "12"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, "PIN shorter than 4 digits must be rejected"


def test_pin_too_long(client, auth_headers):
    resp = client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "123456789"},  # 9 digits — exceeds 8
        headers=auth_headers,
    )
    assert resp.status_code == 422, "PIN longer than 8 digits must be rejected"


# ── 4. Sync key enforcement ───────────────────────────────────────────────────

def test_sync_key_required(client):
    """No X-Api-Key header → 503 (key not configured in test env would be 503,
    but pytest.ini sets SYNC_AGENT_API_KEY=test-sync-key so missing key → 403)."""
    resp = client.post(
        "/api/v1/sync/products",
        json={"records": [], "store_id": 1},
        # No X-Api-Key header
    )
    assert resp.status_code in (403, 503), (
        f"Expected 403 or 503 without API key, got {resp.status_code}"
    )


def test_sync_key_invalid(client):
    resp = client.post(
        "/api/v1/sync/products",
        json={"records": [], "store_id": 1},
        headers={"X-Api-Key": "completely-wrong-key"},
    )
    assert resp.status_code == 403


def test_sync_key_valid(client, sync_headers):
    """Valid key → endpoint processes normally (returns 200 with empty sync)."""
    resp = client.post(
        "/api/v1/sync/products",
        json={"records": [], "store_id": 1},
        headers=sync_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] == 0
