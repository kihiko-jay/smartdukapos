"""Add updated_at to customers, add composite report indexes

Revision ID: 0005
Revises: 0004
Create Date: 2025-03-18 00:00:00

What this migration adds:
  1. customers.updated_at column — required for proper LWW sync conflict resolution
     (sync router already reads updated_at; this adds the column it was missing)
  2. Composite index (store_id, created_at) on transactions — used by daily/monthly
     report queries that always filter by store_id AND date range
  3. Composite index (cashier_id, created_at) on transactions — cashier performance reports
  4. Index on customers.phone — phone is the natural key for sync lookups
  5. Index on stock_movements (product_id, created_at) — stock history pagination

Backward compatibility:
  - updated_at defaults to NULL → existing rows won't break LWW logic (treated as
    always-older, so incoming sync records will overwrite — safe default)
  - All new indexes are non-unique and additive — no data migration required
"""

from alembic import op
import sqlalchemy as sa

revision      = "0005"
down_revision = "0004"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. customers.updated_at — required for LWW sync ─────────────────────
    op.add_column(
        "customers",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=None,
        ),
    )
    # Backfill: set updated_at = created_at for existing rows
    op.execute("""
        UPDATE customers
        SET updated_at = created_at
        WHERE updated_at IS NULL AND created_at IS NOT NULL
    """)

    # ── 2. Composite index (store_id, created_at) on transactions ────────────
    # Used by: /reports/daily, /reports/monthly, dashboard queries
    # These always filter WHERE store_id = $1 AND created_at BETWEEN $2 AND $3
    op.create_index(
        "idx_txn_store_date",
        "transactions",
        ["store_id", "created_at"],
    )

    # ── 3. Composite index (cashier_id, created_at) ───────────────────────────
    # Used by: cashier performance reports
    op.create_index(
        "idx_txn_cashier_date",
        "transactions",
        ["cashier_id", "created_at"],
    )

    # ── 4. Index on customers.phone ──────────────────────────────────────────
    # Sync agent upserts by phone on every sync cycle (hot path)
    op.create_index(
        "idx_customer_phone",
        "customers",
        ["phone"],
    )

    # ── 5. Composite index on stock_movements (product_id, created_at) ───────
    # Used by: /products/{id}/stock-history pagination
    op.create_index(
        "idx_sm_product_date",
        "stock_movements",
        ["product_id", "created_at"],
    )

    # ── 6. Index on transactions (status, created_at) for dashboard ──────────
    # Dashboard queries filter by status = 'completed' + recent date
    op.create_index(
        "idx_txn_status_date",
        "transactions",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_txn_status_date",  table_name="transactions")
    op.drop_index("idx_sm_product_date",  table_name="stock_movements")
    op.drop_index("idx_customer_phone",   table_name="customers")
    op.drop_index("idx_txn_cashier_date", table_name="transactions")
    op.drop_index("idx_txn_store_date",   table_name="transactions")
    op.drop_column("customers", "updated_at")
