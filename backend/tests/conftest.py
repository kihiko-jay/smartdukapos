"""
Test fixtures for DukaPOS v4.

Uses SQLite in-memory DB — no PostgreSQL required to run the suite.
Each test gets a clean DB session that rolls back after the test.

JSONB columns are not natively supported in SQLite; the audit_trail and
sync_log tables use JSON instead. This is acceptable for unit tests — the
behaviour being tested is application logic, not DB-level JSON operators.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.core.deps import get_db
from app.core.security import hash_password, create_access_token
from app.models.employee import Employee, Role
from app.models.subscription import Store, Plan, SubStatus
from app.main import app

# ── SQLite in-memory engine ───────────────────────────────────────────────────
# StaticPool + same_thread=False are required for FastAPI/SQLite compatibility
TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# SQLite doesn't enforce FK constraints by default — enable them
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once per test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """
    Yields a test DB session. Rolls back after each test so tests are isolated.
    Uses a savepoint so nested transactions work correctly.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """FastAPI TestClient wired to the test DB session."""
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_store(db):
    """A premium store for tests that hit require_premium endpoints."""
    store = Store(
        name="Test Store",
        plan=Plan.STARTER,
        sub_status=SubStatus.ACTIVE,
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@pytest.fixture
def test_admin(db, test_store):
    """An active admin employee used for auth-required endpoint tests."""
    emp = Employee(
        store_id=test_store.id,
        full_name="Test Admin",
        email="admin@test.com",
        password=hash_password("testpass123"),
        role=Role.ADMIN,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@pytest.fixture
def auth_headers(test_admin):
    """Bearer token headers for test_admin."""
    token = create_access_token({"sub": str(test_admin.id), "role": test_admin.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sync_headers():
    """X-Api-Key headers for sync agent endpoints."""
    return {"X-Api-Key": "test-sync-key"}
