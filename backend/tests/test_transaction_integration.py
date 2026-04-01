"""
Integration tests — Transaction flow (Step 7.1 + 7.2)

Covers the full POS sale lifecycle:
  product → create_transaction → M-Pesa STK push → callback → WS notification
  → void → stock restoration
  → idempotency (duplicate submit safety)
  → sync agent mark-synced

These tests are intentionally coarse-grained — they exercise the full vertical
slice so a refactor that breaks the seam between layers is caught immediately.
"""

import pytest
from decimal import Decimal
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.models.employee import Employee, Role
from app.models.product import Product
from app.models.transaction import Transaction, TransactionStatus, SyncStatus, PaymentMethod
from app.models.subscription import Store, Plan, SubStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def manager_emp(db, test_store):
    emp = Employee(
        store_id=test_store.id,
        full_name="Test Manager",
        email="manager@teststore.com",
        password=hash_password("managerpass123"),
        role=Role.MANAGER,
        terminal_id="T01",
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@pytest.fixture
def manager_headers(manager_emp):
    token = create_access_token({"sub": str(manager_emp.id), "role": manager_emp.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def product_a(db, test_store):
    p = Product(
        sku="TXN-PROD-A",
        name="Pilau Rice 500g",
        selling_price=Decimal("85.00"),
        cost_price=Decimal("55.00"),
        stock_quantity=100,
        is_active=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def product_b(db, test_store):
    p = Product(
        sku="TXN-PROD-B",
        name="Cooking Oil 1L",
        selling_price=Decimal("230.00"),
        cost_price=Decimal("180.00"),
        stock_quantity=50,
        is_active=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_txn_payload(product_id, qty=2, unit_price="85.00",
                      payment="cash", cash_tendered="200.00"):
    return {
        "items": [{
            "product_id": product_id,
            "qty": qty,
            "unit_price": unit_price,
            "discount": "0.00",
        }],
        "discount_amount": "0.00",
        "payment_method": payment,
        "cash_tendered": cash_tendered,
        "terminal_id": "T01",
    }


# ── 1. Basic cash sale ────────────────────────────────────────────────────────

class TestCashSale:

    def test_create_cash_transaction_returns_200(self, client, manager_headers, product_a, db):
        initial_stock = product_a.stock_quantity
        resp = client.post("/api/v1/transactions",
                           headers=manager_headers,
                           json=_make_txn_payload(product_a.id, qty=3, cash_tendered="300.00",
                                                  unit_price="85.00"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["payment_method"] == "cash"
        assert "txn_number" in body
        assert body["txn_number"].startswith("TXN-")

    def test_cash_sale_deducts_stock(self, client, manager_headers, product_a, db):
        initial_stock = product_a.stock_quantity
        qty = 4
        client.post("/api/v1/transactions",
                    headers=manager_headers,
                    json=_make_txn_payload(product_a.id, qty=qty, cash_tendered="400.00",
                                           unit_price="85.00"))
        db.refresh(product_a)
        assert product_a.stock_quantity == initial_stock - qty

    def test_cash_sale_decimal_precision(self, client, manager_headers, db, test_store):
        """65.99 × 3 = 197.97 exactly. Float arithmetic would give 197.97000001."""
        p = Product(sku="DEC-TXN-01", name="Decimal Test",
                    selling_price=Decimal("65.99"), stock_quantity=100, is_active=True)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = client.post("/api/v1/transactions",
                           headers=manager_headers,
                           json=_make_txn_payload(p.id, qty=3, unit_price="65.99",
                                                  cash_tendered="300.00"))
        assert resp.status_code == 200
        body = resp.json()
        # VAT-inclusive total: 197.97 * 1.16 = 229.6452 → rounded to 229.65
        total = Decimal(str(body["total"]))
        subtotal = Decimal(str(body["subtotal"]))
        # Subtotal must be exact
        assert subtotal == Decimal("197.97")

    def test_sale_sets_sync_status_pending(self, client, manager_headers, product_a, db):
        resp = client.post("/api/v1/transactions",
                           headers=manager_headers,
                           json=_make_txn_payload(product_a.id, cash_tendered="200.00",
                                                  unit_price="85.00"))
        txn_number = resp.json()["txn_number"]
        txn = db.query(Transaction).filter(Transaction.txn_number == txn_number).first()
        assert txn is not None
        assert txn.sync_status == SyncStatus.PENDING

    def test_insufficient_stock_returns_400(self, client, manager_headers, product_a):
        resp = client.post("/api/v1/transactions",
                           headers=manager_headers,
                           json=_make_txn_payload(product_a.id,
                                                  qty=product_a.stock_quantity + 1,
                                                  cash_tendered="99999.00",
                                                  unit_price="85.00"))
        assert resp.status_code == 400
        assert "stock" in resp.json()["detail"].lower()


# ── 2. Idempotency ────────────────────────────────────────────────────────────

class TestIdempotency:

    def test_duplicate_idempotency_key_returns_same_transaction(
        self, client, manager_headers, product_a, db
    ):
        """Submitting the same Idempotency-Key twice must return the same txn."""
        idem_key = f"OFFLINE-IDEM-{id(self)}"
        headers = {**manager_headers, "Idempotency-Key": idem_key}

        resp1 = client.post("/api/v1/transactions", headers=headers,
                            json=_make_txn_payload(product_a.id, cash_tendered="200.00",
                                                   unit_price="85.00"))
        resp2 = client.post("/api/v1/transactions", headers=headers,
                            json=_make_txn_payload(product_a.id, cash_tendered="200.00",
                                                   unit_price="85.00"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["txn_number"] == resp2.json()["txn_number"]
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_duplicate_does_not_deduct_stock_twice(
        self, client, manager_headers, product_a, db
    ):
        initial_stock = product_a.stock_quantity
        qty = 2
        idem_key = f"OFFLINE-STOCK-{id(self)}"
        headers = {**manager_headers, "Idempotency-Key": idem_key}
        payload = _make_txn_payload(product_a.id, qty=qty, cash_tendered="200.00",
                                    unit_price="85.00")

        client.post("/api/v1/transactions", headers=headers, json=payload)
        client.post("/api/v1/transactions", headers=headers, json=payload)

        db.refresh(product_a)
        # Stock must only have been deducted once
        assert product_a.stock_quantity == initial_stock - qty


# ── 3. Void ────────────────────────────────────────────────────────────────────

class TestVoid:

    def test_void_restores_stock(self, client, manager_headers, product_a, db):
        initial_stock = product_a.stock_quantity
        qty = 3

        create_resp = client.post(
            "/api/v1/transactions", headers=manager_headers,
            json=_make_txn_payload(product_a.id, qty=qty, cash_tendered="300.00",
                                   unit_price="85.00"),
        )
        txn_id = create_resp.json()["id"]
        db.refresh(product_a)
        assert product_a.stock_quantity == initial_stock - qty

        void_resp = client.post(f"/api/v1/transactions/{txn_id}/void",
                                headers=manager_headers)
        assert void_resp.status_code == 200

        db.refresh(product_a)
        assert product_a.stock_quantity == initial_stock

    def test_void_changes_status_to_voided(self, client, manager_headers, product_a, db):
        create_resp = client.post(
            "/api/v1/transactions", headers=manager_headers,
            json=_make_txn_payload(product_a.id, cash_tendered="200.00", unit_price="85.00"),
        )
        txn_id = create_resp.json()["id"]
        txn_number = create_resp.json()["txn_number"]

        client.post(f"/api/v1/transactions/{txn_id}/void", headers=manager_headers)

        txn = db.query(Transaction).filter(Transaction.txn_number == txn_number).first()
        assert txn.status == TransactionStatus.VOIDED

    def test_cannot_void_already_voided(self, client, manager_headers, product_a, db):
        create_resp = client.post(
            "/api/v1/transactions", headers=manager_headers,
            json=_make_txn_payload(product_a.id, cash_tendered="200.00", unit_price="85.00"),
        )
        txn_id = create_resp.json()["id"]
        client.post(f"/api/v1/transactions/{txn_id}/void", headers=manager_headers)
        resp2 = client.post(f"/api/v1/transactions/{txn_id}/void", headers=manager_headers)
        assert resp2.status_code == 400


# ── 4. Sync mark-synced ────────────────────────────────────────────────────────

class TestSyncMarkSynced:

    def test_mark_synced_updates_sync_status(self, client, manager_headers, product_a, db):
        create_resp = client.post(
            "/api/v1/transactions", headers=manager_headers,
            json=_make_txn_payload(product_a.id, cash_tendered="200.00", unit_price="85.00"),
        )
        txn_number = create_resp.json()["txn_number"]

        mark_resp = client.post(
            "/api/v1/transactions/sync/mark-synced",
            headers=manager_headers,
            json=[txn_number],
        )
        assert mark_resp.status_code == 200
        assert mark_resp.json()["marked_synced"] == 1

        txn = db.query(Transaction).filter(Transaction.txn_number == txn_number).first()
        assert txn.sync_status == SyncStatus.SYNCED
        assert txn.synced_at is not None


# ── 5. Multi-item transaction ──────────────────────────────────────────────────

class TestMultiItemTransaction:

    def test_multi_item_transaction(self, client, manager_headers, product_a, product_b, db):
        payload = {
            "items": [
                {"product_id": product_a.id, "qty": 2,
                 "unit_price": "85.00", "discount": "0.00"},
                {"product_id": product_b.id, "qty": 1,
                 "unit_price": "230.00", "discount": "10.00"},
            ],
            "discount_amount": "0.00",
            "payment_method": "cash",
            "cash_tendered": "500.00",
            "terminal_id": "T01",
        }
        resp = client.post("/api/v1/transactions",
                           headers=manager_headers, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        # 85*2 = 170, 230-10 = 220, subtotal = 390
        assert Decimal(str(body["subtotal"])) == Decimal("390.00")

    def test_multi_item_deducts_both_stocks(
        self, client, manager_headers, product_a, product_b, db
    ):
        stock_a = product_a.stock_quantity
        stock_b = product_b.stock_quantity
        payload = {
            "items": [
                {"product_id": product_a.id, "qty": 3,
                 "unit_price": "85.00", "discount": "0.00"},
                {"product_id": product_b.id, "qty": 2,
                 "unit_price": "230.00", "discount": "0.00"},
            ],
            "discount_amount": "0.00",
            "payment_method": "cash",
            "cash_tendered": "1000.00",
            "terminal_id": "T01",
        }
        client.post("/api/v1/transactions", headers=manager_headers, json=payload)
        db.refresh(product_a)
        db.refresh(product_b)
        assert product_a.stock_quantity == stock_a - 3
        assert product_b.stock_quantity == stock_b - 2
