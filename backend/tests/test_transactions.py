"""
Transaction tests — Decimal precision, VAT arithmetic, sync status, void, idempotency.

These tests directly validate the FLOAT→NUMERIC fix. A float-based system
would produce results like 197.97000001; these assertions catch regressions.
"""

import pytest
from decimal import Decimal
from app.models.product import Product, Category
from app.models.transaction import Transaction, TransactionStatus, SyncStatus
from app.core.security import create_access_token


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_product(db, store_id, sku="TEST-SKU-001", price="65.99", stock=100):
    """Create a minimal product for transaction tests."""
    product = Product(
        sku=sku,
        name="Test Product",
        selling_price=Decimal(price),
        cost_price=Decimal("40.00"),
        stock_quantity=stock,
        store_id=store_id,
        is_active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def create_txn_payload(product_id, qty=3, unit_price="65.99",
                       payment_method="cash", cash_tendered="300.00"):
    return {
        "items": [
            {
                "product_id": product_id,
                "qty": qty,
                "unit_price": unit_price,
                "discount": "0.00",
            }
        ],
        "discount_amount": "0.00",
        "payment_method": payment_method,
        "cash_tendered": cash_tendered,
        "terminal_id": "T01",
    }


# ── 1. Decimal precision ──────────────────────────────────────────────────────

def test_decimal_precision(client, auth_headers, db, test_admin, test_store):
    """
    65.99 * 3 must equal exactly 197.97 — not 197.97000001 or similar float artifact.
    This directly validates the FLOAT → NUMERIC(12,2) migration.
    """
    product = make_product(db, test_store.id, sku="DEC-001", price="65.99")

    resp = client.post(
        "/api/v1/transactions",
        json=create_txn_payload(product.id, qty=3, unit_price="65.99"),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Find the line item and check line_total
    items = data.get("items", [])
    assert items, "Response must include items"
    line_total_str = str(items[0]["line_total"])

    # Must be exactly 197.97 — no float garbage
    assert line_total_str == "197.97", (
        f"Expected '197.97', got '{line_total_str}' — NUMERIC precision failure"
    )
    assert "197.97000" not in line_total_str, "Float artifact detected in line_total"


# ── 2. VAT calculation ────────────────────────────────────────────────────────

def test_vat_calculation(client, auth_headers, db, test_admin, test_store):
    """VAT must be exactly round(subtotal * 0.16, 2) using Decimal arithmetic."""
    product = make_product(db, test_store.id, sku="VAT-001", price="100.00")

    resp = client.post(
        "/api/v1/transactions",
        json=create_txn_payload(product.id, qty=1, unit_price="100.00", cash_tendered="200.00"),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    subtotal   = Decimal(str(data["subtotal"]))
    vat_amount = Decimal(str(data["vat_amount"]))
    expected   = (subtotal * Decimal("0.16")).quantize(Decimal("0.01"))

    assert vat_amount == expected, (
        f"VAT mismatch: got {vat_amount}, expected {expected}"
    )


# ── 3. Sync status default ────────────────────────────────────────────────────

def test_sync_status_default(client, auth_headers, db, test_admin, test_store):
    """New transactions must default to sync_status='pending'."""
    product = make_product(db, test_store.id, sku="SYNC-001", price="50.00")

    resp = client.post(
        "/api/v1/transactions",
        json=create_txn_payload(product.id, qty=1, unit_price="50.00", cash_tendered="100.00"),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    txn_number = resp.json()["txn_number"]

    txn = db.query(Transaction).filter(Transaction.txn_number == txn_number).first()
    assert txn is not None
    assert txn.sync_status == SyncStatus.PENDING, (
        f"Expected sync_status=pending, got {txn.sync_status}"
    )


# ── 4. Void restores stock ────────────────────────────────────────────────────

def test_void_restores_stock(client, auth_headers, db, test_admin, test_store):
    """Voiding a transaction must restore the product's stock_quantity."""
    product = make_product(db, test_store.id, sku="VOID-001", price="25.00", stock=10)

    # Create and complete a transaction for qty=2
    resp = client.post(
        "/api/v1/transactions",
        json=create_txn_payload(product.id, qty=2, unit_price="25.00", cash_tendered="100.00"),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    txn_id = resp.json()["id"]

    # Stock should be 8 after the sale
    db.refresh(product)
    assert product.stock_quantity == 8, f"Expected 8 after sale, got {product.stock_quantity}"

    # Void the transaction
    resp = client.post(
        f"/api/v1/transactions/{txn_id}/void",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Stock should be restored to 10
    db.refresh(product)
    assert product.stock_quantity == 10, (
        f"Expected stock restored to 10 after void, got {product.stock_quantity}"
    )


# ── 5. Sync idempotency ───────────────────────────────────────────────────────

def test_transaction_idempotent_sync(client, sync_headers, db, test_store):
    """
    Posting the same transaction to /sync/transactions twice must result in
    exactly one record in the DB — not two.
    """
    txn_record = {
        "txn_number": "TXN-IDEM-0001",
        "store_id": test_store.id,
        "subtotal": "100.00",
        "discount_amount": "0.00",
        "vat_amount": "16.00",
        "total": "116.00",
        "payment_method": "cash",
        "cash_tendered": "120.00",
        "change_given": "4.00",
        "status": "completed",
        "items": [
            {
                "product_id": None,
                "product_name": "Test Item",
                "sku": "TEST-SKU",
                "qty": 1,
                "unit_price": "100.00",
                "line_total": "100.00",
            }
        ],
    }

    # First POST
    resp1 = client.post(
        "/api/v1/sync/transactions",
        json={"records": [txn_record], "store_id": test_store.id},
        headers=sync_headers,
    )
    assert resp1.status_code == 200

    # Second POST — same record
    resp2 = client.post(
        "/api/v1/sync/transactions",
        json={"records": [txn_record], "store_id": test_store.id},
        headers=sync_headers,
    )
    assert resp2.status_code == 200

    # Only one record must exist
    count = (
        db.query(Transaction)
        .filter(Transaction.txn_number == "TXN-IDEM-0001")
        .count()
    )
    assert count == 1, f"Expected 1 transaction after duplicate sync, found {count}"
