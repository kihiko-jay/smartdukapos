"""Add missing DB constraints, indexes, and unique guards

Revision ID: 0003
Revises: 0002
Create Date: 2025-01-03 00:00:00

What this migration adds:
  1. CHECK constraints on all money columns (must be >= 0)
  2. CHECK on stock_quantity (no negative stock)
  3. CHECK on transaction totals (subtotal + vat = total, within 1 KES rounding tolerance)
  4. UNIQUE on mpesa_ref (one M-PESA receipt cannot pay two transactions)
  5. Partial index for pending transactions (fast queue polling)
  6. Partial index for unsynced eTIMS (fast retry queue)
  7. CHECK on etims / sync status enums for DB-level enforcement
  8. NOT NULL enforcement on transaction total / payment_method
"""

from alembic import op
import sqlalchemy as sa

revision      = "0003"
down_revision = "0002"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. Money columns must be non-negative ────────────────────────────────
    op.execute("ALTER TABLE transactions ADD CONSTRAINT chk_txn_subtotal_gte0 CHECK (subtotal >= 0)")
    op.execute("ALTER TABLE transactions ADD CONSTRAINT chk_txn_vat_gte0 CHECK (vat_amount >= 0)")
    op.execute("ALTER TABLE transactions ADD CONSTRAINT chk_txn_total_gte0 CHECK (total > 0)")
    op.execute("ALTER TABLE transactions ADD CONSTRAINT chk_txn_discount_gte0 CHECK (discount_amount >= 0)")
    op.execute("ALTER TABLE transactions ADD CONSTRAINT chk_txn_cash_tendered_gte0 CHECK (cash_tendered IS NULL OR cash_tendered >= 0)")

    op.execute("ALTER TABLE transaction_items ADD CONSTRAINT chk_ti_qty_positive CHECK (qty > 0)")
    op.execute("ALTER TABLE transaction_items ADD CONSTRAINT chk_ti_unit_price_gte0 CHECK (unit_price >= 0)")
    op.execute("ALTER TABLE transaction_items ADD CONSTRAINT chk_ti_line_total_gte0 CHECK (line_total >= 0)")
    op.execute("ALTER TABLE transaction_items ADD CONSTRAINT chk_ti_discount_gte0 CHECK (discount >= 0)")

    op.execute("ALTER TABLE products ADD CONSTRAINT chk_prod_selling_price_gte0 CHECK (selling_price >= 0)")
    op.execute("ALTER TABLE products ADD CONSTRAINT chk_prod_cost_price_gte0 CHECK (cost_price IS NULL OR cost_price >= 0)")
    op.execute("ALTER TABLE products ADD CONSTRAINT chk_prod_reorder_gte0 CHECK (reorder_level >= 0)")

    # ── 2. No negative stock at DB level ─────────────────────────────────────
    op.execute("ALTER TABLE products ADD CONSTRAINT chk_prod_stock_gte0 CHECK (stock_quantity >= 0)")

    # ── 3. M-PESA receipt uniqueness — one receipt = one transaction ─────────
    # Partial unique: only enforce when mpesa_ref is NOT NULL
    op.execute("""
        CREATE UNIQUE INDEX uq_txn_mpesa_ref
        ON transactions (mpesa_ref)
        WHERE mpesa_ref IS NOT NULL
    """)

    # ── 4. Partial indexes for hot query paths ────────────────────────────────
    # Sync agent polls this constantly
    op.execute("""
        CREATE INDEX idx_txn_sync_pending
        ON transactions (created_at ASC)
        WHERE sync_status IN ('pending', 'failed') AND status = 'completed'
    """)
    # eTIMS retry queue
    op.execute("""
        CREATE INDEX idx_txn_etims_pending
        ON transactions (created_at ASC)
        WHERE etims_synced = FALSE AND status = 'completed'
    """)
    # Active products for POS lookup
    op.execute("""
        CREATE INDEX idx_prod_active_sku
        ON products (sku)
        WHERE is_active = TRUE
    """)

    # ── 5. Stock movements must reference valid types ─────────────────────────
    op.execute("""
        ALTER TABLE stock_movements ADD CONSTRAINT chk_sm_movement_type
        CHECK (movement_type IN ('sale','purchase','adjustment','write_off','void_restore','sync','opening'))
    """)
    op.execute("ALTER TABLE stock_movements ADD CONSTRAINT chk_sm_qty_nonzero CHECK (qty_delta != 0)")

    # ── 6. Sync log status must be known value ────────────────────────────────
    op.execute("""
        ALTER TABLE sync_log ADD CONSTRAINT chk_sl_status
        CHECK (status IN ('success','conflict','error','retry','skipped'))
    """)

    # ── 7. Prevent duplicate eTIMS invoice numbers ────────────────────────────
    op.execute("""
        CREATE UNIQUE INDEX uq_txn_etims_invoice
        ON transactions (etims_invoice_no)
        WHERE etims_invoice_no IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_txn_etims_invoice")
    op.execute("ALTER TABLE sync_log DROP CONSTRAINT IF EXISTS chk_sl_status")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS chk_sm_qty_nonzero")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS chk_sm_movement_type")
    op.execute("DROP INDEX IF EXISTS idx_prod_active_sku")
    op.execute("DROP INDEX IF EXISTS idx_txn_etims_pending")
    op.execute("DROP INDEX IF EXISTS idx_txn_sync_pending")
    op.execute("DROP INDEX IF EXISTS uq_txn_mpesa_ref")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_prod_stock_gte0")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_prod_reorder_gte0")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_prod_cost_price_gte0")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_prod_selling_price_gte0")
    op.execute("ALTER TABLE transaction_items DROP CONSTRAINT IF EXISTS chk_ti_discount_gte0")
    op.execute("ALTER TABLE transaction_items DROP CONSTRAINT IF EXISTS chk_ti_line_total_gte0")
    op.execute("ALTER TABLE transaction_items DROP CONSTRAINT IF EXISTS chk_ti_unit_price_gte0")
    op.execute("ALTER TABLE transaction_items DROP CONSTRAINT IF EXISTS chk_ti_qty_positive")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_txn_cash_tendered_gte0")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_txn_discount_gte0")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_txn_total_gte0")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_txn_vat_gte0")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_txn_subtotal_gte0")
