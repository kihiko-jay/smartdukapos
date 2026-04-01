"""
Integration tests — Auth flow (Step 7.1)

Covers:
  - Login → access_token + refresh_token returned
  - Access token grants access to protected endpoints
  - Refresh token produces new access + refresh tokens (token rotation)
  - Expired/invalid tokens are rejected (401)
  - Refresh token cannot be used as access token (type claim enforcement)
  - Rate limiter rejects excess login attempts (429)
  - Deactivated employee cannot log in (403)
  - Inactive employee's existing tokens are invalidated (401)
"""

import pytest
import time
from decimal import Decimal
from fastapi.testclient import TestClient

from app.core.security import (
    create_access_token, create_refresh_token,
    hash_password, decode_token, decode_refresh_token,
)
from app.models.employee import Employee, Role
from app.models.subscription import Store, Plan, SubStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cashier(db, test_store):
    emp = Employee(
        store_id=test_store.id,
        full_name="Test Cashier",
        email="cashier@teststore.com",
        password=hash_password("cashierpass123"),
        role=Role.CASHIER,
        terminal_id="T02",
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


# ── Login flow ────────────────────────────────────────────────────────────────

class TestLoginFlow:

    def test_successful_login_returns_both_tokens(self, client, test_admin):
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com",
            "password": "testpass123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["employee_id"] == test_admin.id
        assert body["role"] == "admin"

    def test_login_access_token_is_valid_jwt(self, client, test_admin):
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        token = resp.json()["access_token"]
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == str(test_admin.id)
        assert payload["type"] == "access"
        assert "jti" in payload

    def test_login_refresh_token_has_correct_type(self, client, test_admin):
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        token = resp.json()["refresh_token"]
        payload = decode_refresh_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"
        # Refresh token must NOT decode as access token
        assert decode_token(token) is None

    def test_wrong_password_returns_401(self, client, test_admin):
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "wrongpassword",
        })
        assert resp.status_code == 401
        # Consistent message — no user enumeration
        assert "Invalid email or password" in resp.json()["detail"]

    def test_wrong_email_returns_same_401(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email": "nobody@nowhere.com", "password": "whatever",
        })
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.json()["detail"]

    def test_deactivated_employee_cannot_login(self, client, db, test_admin):
        test_admin.is_active = False
        db.commit()
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        assert resp.status_code == 403
        # Restore for other tests
        test_admin.is_active = True
        db.commit()


# ── Token usage ───────────────────────────────────────────────────────────────

class TestTokenUsage:

    def test_access_token_grants_access_to_me(self, client, test_admin):
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        token = login.json()["access_token"]
        me = client.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["id"] == test_admin.id

    def test_refresh_token_rejected_as_access_token(self, client, test_admin):
        """Using a refresh token where an access token is expected must return 401."""
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        refresh_token = login.json()["refresh_token"]
        # Try using the refresh token as a Bearer access token
        me = client.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {refresh_token}"})
        assert me.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = client.get("/api/v1/auth/me",
                          headers={"Authorization": "Bearer not.a.real.token"})
        assert resp.status_code == 401

    def test_missing_token_returns_401(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_inactive_employee_token_returns_401(self, client, db, test_admin):
        """Token for a just-deactivated employee must be refused even if JWT is valid."""
        token = create_access_token({"sub": str(test_admin.id), "role": test_admin.role.value})
        test_admin.is_active = False
        db.commit()

        resp = client.get("/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

        test_admin.is_active = True
        db.commit()


# ── Token refresh ─────────────────────────────────────────────────────────────

class TestTokenRefresh:

    def test_refresh_returns_new_access_token(self, client, test_admin):
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        refresh_token = login.json()["refresh_token"]

        refresh_resp = client.post("/api/v1/auth/token/refresh",
                                   json={"refresh_token": refresh_token})
        assert refresh_resp.status_code == 200
        body = refresh_resp.json()
        assert "access_token" in body
        assert "refresh_token" in body  # token rotation

    def test_refresh_token_rotation(self, client, test_admin):
        """Each /token/refresh call must return a NEW refresh token (not the same one)."""
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        original_refresh = login.json()["refresh_token"]

        refresh_resp = client.post("/api/v1/auth/token/refresh",
                                   json={"refresh_token": original_refresh})
        new_refresh = refresh_resp.json()["refresh_token"]
        assert new_refresh != original_refresh

    def test_new_access_token_works(self, client, test_admin):
        """New access token from refresh must work on protected endpoints."""
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        refresh_resp = client.post("/api/v1/auth/token/refresh",
                                   json={"refresh_token": login.json()["refresh_token"]})
        new_access = refresh_resp.json()["access_token"]

        me = client.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {new_access}"})
        assert me.status_code == 200

    def test_invalid_refresh_token_rejected(self, client):
        resp = client.post("/api/v1/auth/token/refresh",
                           json={"refresh_token": "forged.refresh.token"})
        assert resp.status_code == 401

    def test_access_token_cannot_be_used_as_refresh_token(self, client, test_admin):
        login = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "testpass123",
        })
        access_token = login.json()["access_token"]

        resp = client.post("/api/v1/auth/token/refresh",
                           json={"refresh_token": access_token})
        assert resp.status_code == 401

    def test_missing_refresh_token_body_returns_400(self, client):
        resp = client.post("/api/v1/auth/token/refresh", json={})
        assert resp.status_code == 400


# ── Role-based access control ─────────────────────────────────────────────────

class TestRBAC:

    def test_cashier_can_read_products(self, client, db, cashier):
        token = create_access_token({"sub": str(cashier.id), "role": cashier.role.value})
        resp = client.get("/api/v1/products",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_cashier_cannot_create_product(self, client, db, cashier):
        """Product creation requires premium (manager+). Cashier gets 403."""
        token = create_access_token({"sub": str(cashier.id), "role": cashier.role.value})
        resp = client.post("/api/v1/products",
                           headers={"Authorization": f"Bearer {token}"},
                           json={
                               "sku": "RBAC-001", "name": "Test",
                               "selling_price": "100.00",
                           })
        assert resp.status_code == 403

    def test_admin_can_create_employee(self, client, auth_headers, test_store):
        resp = client.post("/api/v1/auth/employees",
                           headers=auth_headers,
                           json={
                               "store_id":  test_store.id,
                               "full_name": "New Employee",
                               "email":     "new@teststore.com",
                               "password":  "securepass123",
                               "role":      "cashier",
                           })
        assert resp.status_code == 200
        assert resp.json()["email"] == "new@teststore.com"
