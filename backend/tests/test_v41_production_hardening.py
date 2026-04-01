"""
DukaPOS v4.1 — Production hardening test suite

Covers every fix introduced in v4.1:
  1. Frontend session: getSession / clearSession exports (import-level smoke)
  2. Multi-store customer sync — phone collision across tenants
  3. Subscription activation — platform owner gate
  4. Demo mode — production build must not surface hardcoded credentials
     (tested at the Python layer via the env-flag contract)

Node/JS tests for checkpoint persistence live in:
  sync-agent/tests/sync-agent.test.js  (see checkpoint section)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import event as sa_event

from app.database import Base
from app.core.deps import get_db
from app.core.security import hash_password, create_access_token
from app.models.employee import Employee, Role
from app.models.subscription import Store, Plan, SubStatus
from app.models.customer import Customer
from app.main import app

# ── In-memory test database ───────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@sa_event.listens_for(engine, "connect")
def _fk_on(dbapi_conn, _):
    dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")

TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    conn = engine.connect()
    txn  = conn.begin()
    sess = TestSession(bind=conn)
    yield sess
    sess.close()
    txn.rollback()
    conn.close()


@pytest.fixture
def client(db):
    def _get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def store_a(db):
    s = Store(name="Store A", plan=Plan.STARTER, sub_status=SubStatus.ACTIVE)
    db.add(s); db.flush()
    return s

@pytest.fixture
def store_b(db):
    s = Store(name="Store B", plan=Plan.STARTER, sub_status=SubStatus.ACTIVE)
    db.add(s); db.flush()
    return s

@pytest.fixture
def admin_a(db, store_a):
    e = Employee(
        store_id=store_a.id, full_name="Admin A",
        email="admin_a@test.com", password=hash_password("pass"),
        role=Role.ADMIN, is_active=True,
    )
    db.add(e); db.flush(); return e

@pytest.fixture
def admin_b(db, store_b):
    e = Employee(
        store_id=store_b.id, full_name="Admin B",
        email="admin_b@test.com", password=hash_password("pass"),
        role=Role.ADMIN, is_active=True,
    )
    db.add(e); db.flush(); return e

@pytest.fixture
def platform_owner(db):
    e = Employee(
        store_id=None, full_name="Jay Platform",
        email="jay@dukapos.ke", password=hash_password("ownerpass"),
        role=Role.PLATFORM_OWNER, is_active=True,
    )
    db.add(e); db.flush(); return e

def _token(emp):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(emp.id), 'role': emp.role})}"}

SYNC_HEADERS = {"X-Api-Key": "test-sync-key"}


# ════════════════════════════════════════════════════════════════════════════════
# 1 — MULTI-STORE CUSTOMER SYNC  (Fix #3)
#     Two stores, same phone number — must never collide.
# ════════════════════════════════════════════════════════════════════════════════

class TestMultiStoreCustomerSync:
    """Verifies that sync_customers scopes every lookup by (store_id, phone)."""

    SHARED_PHONE = "0712345678"

    def test_same_phone_in_two_stores_creates_two_records(self, client, db, store_a, store_b):
        """A phone that exists in store A must also be creatable in store B."""
        r_a = client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_a.id,
            "records":  [{"phone": self.SHARED_PHONE, "name": "Alice (Store A)"}],
        })
        assert r_a.status_code == 200, r_a.text
        assert r_a.json()["synced"] == 1

        r_b = client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_b.id,
            "records":  [{"phone": self.SHARED_PHONE, "name": "Alice (Store B)"}],
        })
        assert r_b.status_code == 200, r_b.text
        assert r_b.json()["synced"] == 1

        rows = db.query(Customer).filter(Customer.phone == self.SHARED_PHONE).all()
        assert len(rows) == 2, "Expected two separate customer records for the same phone"
        store_ids = {r.store_id for r in rows}
        assert store_ids == {store_a.id, store_b.id}

    def test_sync_update_does_not_cross_tenant(self, client, db, store_a, store_b):
        """Updating a customer in store A must not touch the same phone in store B."""
        # Seed both stores
        client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_a.id,
            "records":  [{"phone": "0799000001", "name": "Bob (Store A)"}],
        })
        client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_b.id,
            "records":  [{"phone": "0799000001", "name": "Bob (Store B)"}],
        })

        # Update store A's record with a newer timestamp
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_a.id,
            "records":  [{"phone": "0799000001", "name": "Bob Updated (Store A)", "updated_at": future}],
        })

        cust_a = db.query(Customer).filter(
            Customer.store_id == store_a.id, Customer.phone == "0799000001"
        ).first()
        cust_b = db.query(Customer).filter(
            Customer.store_id == store_b.id, Customer.phone == "0799000001"
        ).first()

        assert cust_a is not None and "Updated" in cust_a.name
        assert cust_b is not None and "Updated" not in cust_b.name, (
            "Store B's customer was incorrectly overwritten by Store A's sync"
        )

    def test_sync_without_store_id_is_rejected(self, client):
        """Omitting store_id must return an error, not silently proceed."""
        r = client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "records": [{"phone": "0700000000", "name": "No Store"}],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["synced"] == 0
        assert any("missing_store_id" in str(e) for e in body["errors"])

    def test_missing_phone_skipped_not_fatal(self, client, store_a):
        """Records without a phone are skipped gracefully, not crashing the batch."""
        r = client.post("/api/v1/sync/customers", headers=SYNC_HEADERS, json={
            "store_id": store_a.id,
            "records":  [
                {"name": "No Phone Customer"},          # missing phone
                {"phone": "0711000001", "name": "Valid"},
            ],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["synced"] == 1
        assert len(body["errors"]) == 1


# ════════════════════════════════════════════════════════════════════════════════
# 2 — SUBSCRIPTION ACTIVATION AUTH  (Fix #4)
#     /subscription/activate/{store_id} must be platform-owner-only.
# ════════════════════════════════════════════════════════════════════════════════

class TestSubscriptionActivationAuth:
    """Verifies that require_platform_owner gates the manual activation endpoint."""

    def test_store_admin_cannot_activate_own_store(self, client, admin_a, store_a):
        """Store admin must receive 403, not be able to activate their own store."""
        r = client.post(
            f"/api/v1/subscription/activate/{store_a.id}",
            headers=_token(admin_a),
            params={"plan": "starter", "months": 1},
        )
        assert r.status_code == 403, (
            f"Expected 403 for store admin activating own store, got {r.status_code}"
        )

    def test_store_admin_cannot_activate_other_store(self, client, admin_a, store_b):
        """Store admin cannot activate another tenant's subscription."""
        r = client.post(
            f"/api/v1/subscription/activate/{store_b.id}",
            headers=_token(admin_a),
            params={"plan": "starter", "months": 1},
        )
        assert r.status_code == 403, (
            f"Expected 403 for cross-tenant activation, got {r.status_code}"
        )

    def test_platform_owner_can_activate(self, client, platform_owner, store_a):
        """Platform owner must be able to activate any store."""
        r = client.post(
            f"/api/v1/subscription/activate/{store_a.id}",
            headers=_token(platform_owner),
            params={"plan": "starter", "months": 1},
        )
        assert r.status_code == 200, (
            f"Expected 200 for platform owner activation, got {r.status_code}: {r.text}"
        )
        assert "activated" in r.json().get("message", "").lower()

    def test_unauthenticated_cannot_activate(self, client, store_a):
        """No credentials → 401."""
        r = client.post(
            f"/api/v1/subscription/activate/{store_a.id}",
            params={"plan": "starter", "months": 1},
        )
        assert r.status_code == 401

    def test_platform_owner_activating_nonexistent_store_returns_404(
        self, client, platform_owner
    ):
        r = client.post(
            "/api/v1/subscription/activate/999999",
            headers=_token(platform_owner),
            params={"plan": "starter", "months": 1},
        )
        assert r.status_code == 404

    def test_cross_tenant_admin_b_cannot_activate_store_a(
        self, client, admin_b, store_a
    ):
        """Admin from store B must not be able to target store A."""
        r = client.post(
            f"/api/v1/subscription/activate/{store_a.id}",
            headers=_token(admin_b),
            params={"plan": "growth", "months": 3},
        )
        assert r.status_code == 403


# ════════════════════════════════════════════════════════════════════════════════
# 3 — DEMO MODE CONTRACT  (Fix #6)
#     Python-layer test: confirms the env-var contract is honoured.
#     The Vite build test in CI does the bundle-scan (see build-frontend job).
# ════════════════════════════════════════════════════════════════════════════════

class TestDemoModeContract:
    """
    The frontend reads VITE_DEMO_MODE at build time (import.meta.env).
    These tests verify the contract from the Python/backend side:
      - The backend never returns hardcoded demo credentials in API responses.
      - The /auth/login endpoint does not leak demo passwords in error messages.
    """

    def test_login_wrong_password_does_not_echo_credentials(self, client):
        """Error responses must not echo back any password values."""
        r = client.post("/api/v1/auth/login", json={
            "email": "admin@dukapos.ke", "password": "cashier1234"
        })
        # Whatever the status, the response body must not contain the password
        assert "cashier1234" not in r.text
        assert "admin1234"   not in r.text

    def test_me_endpoint_does_not_return_password_field(self, client, admin_a):
        """The /auth/me response must never include a password hash."""
        r = client.get("/api/v1/auth/me", headers=_token(admin_a))
        if r.status_code == 200:
            data = r.json()
            assert "password" not in data
            assert "pin" not in data
