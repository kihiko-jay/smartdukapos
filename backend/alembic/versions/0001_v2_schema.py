"""v2 schema — NUMERIC precision, new tables, sync columns

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

What this migration does:
  1. Converts all money columns from FLOAT to NUMERIC(12,2) — eliminates
     KES rounding errors (e.g. 65.99 * 3 = 197.97, not 197.97000001)
  2. Adds sync_status / synced_at columns to transactions
  3. Widens employees.pin to hold a bcrypt hash (was VARCHAR(10))
  4. Creates: suppliers, stock_movements, audit_trail, sync_log tables

downgrade() reverses all changes. Column type reversions go back to FLOAT
(the original state before v2). New tables are dropped.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── a–g: transaction_items money columns → NUMERIC(12,2) ────────────────
    op.execute(
        "ALTER TABLE transaction_items ALTER COLUMN unit_price TYPE NUMERIC(12,2) "
        "USING unit_price::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transaction_items ALTER COLUMN cost_price_snap TYPE NUMERIC(12,2) "
        "USING cost_price_snap::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transaction_items ALTER COLUMN discount TYPE NUMERIC(12,2) "
        "USING discount::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transaction_items ALTER COLUMN vat_amount TYPE NUMERIC(12,2) "
        "USING vat_amount::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transaction_items ALTER COLUMN line_total TYPE NUMERIC(12,2) "
        "USING line_total::NUMERIC(12,2)"
    )

    # ── a–b: products money columns → NUMERIC(12,2) ─────────────────────────
    op.execute(
        "ALTER TABLE products ALTER COLUMN selling_price TYPE NUMERIC(12,2) "
        "USING selling_price::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE products ALTER COLUMN cost_price TYPE NUMERIC(12,2) "
        "USING cost_price::NUMERIC(12,2)"
    )

    # ── h–m: transactions money columns → NUMERIC(12,2) ─────────────────────
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN subtotal TYPE NUMERIC(12,2) "
        "USING subtotal::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN discount_amount TYPE NUMERIC(12,2) "
        "USING discount_amount::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN vat_amount TYPE NUMERIC(12,2) "
        "USING vat_amount::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN total TYPE NUMERIC(12,2) "
        "USING total::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN cash_tendered TYPE NUMERIC(12,2) "
        "USING cash_tendered::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN change_given TYPE NUMERIC(12,2) "
        "USING change_given::NUMERIC(12,2)"
    )

    # ── n–o: add sync tracking columns to transactions ───────────────────────
    op.execute(
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
        "sync_status VARCHAR(20) DEFAULT 'pending'"
    )
    op.execute(
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
        "synced_at TIMESTAMPTZ"
    )

    # ── p: widen employees.pin for bcrypt hash ───────────────────────────────
    op.execute(
        "ALTER TABLE employees ALTER COLUMN pin TYPE VARCHAR(200)"
    )

    # ── q: suppliers table ───────────────────────────────────────────────────
    op.create_table(
        "suppliers",
        sa.Column("id",           sa.Integer(),     primary_key=True),
        sa.Column("store_id",     sa.Integer(),     sa.ForeignKey("stores.id"), nullable=True),
        sa.Column("name",         sa.String(200),   nullable=False),
        sa.Column("contact_name", sa.String(150),   nullable=True),
        sa.Column("phone",        sa.String(20),    nullable=True),
        sa.Column("email",        sa.String(200),   nullable=True),
        sa.Column("address",      sa.Text(),        nullable=True),
        sa.Column("kra_pin",      sa.String(50),    nullable=True),
        sa.Column("is_active",    sa.Boolean(),     default=True),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    # ── r: stock_movements table ─────────────────────────────────────────────
    op.create_table(
        "stock_movements",
        sa.Column("id",            sa.Integer(),    primary_key=True),
        sa.Column("product_id",    sa.Integer(),    sa.ForeignKey("products.id"),  nullable=False),
        sa.Column("store_id",      sa.Integer(),    sa.ForeignKey("stores.id"),    nullable=True),
        sa.Column("movement_type", sa.String(20),   nullable=False),
        sa.Column("qty_delta",     sa.Integer(),    nullable=False),
        sa.Column("qty_before",    sa.Integer(),    nullable=False),
        sa.Column("qty_after",     sa.Integer(),    nullable=False),
        sa.Column("ref_id",        sa.String(50),   nullable=True),
        sa.Column("notes",         sa.Text(),       nullable=True),
        sa.Column("performed_by",  sa.Integer(),    sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), index=True),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])

    # ── s: audit_trail table (BIGSERIAL id, JSONB for before/after vals) ─────
    op.create_table(
        "audit_trail",
        sa.Column("id",         sa.BigInteger(),  primary_key=True),
        sa.Column("store_id",   sa.Integer(),     sa.ForeignKey("stores.id"), nullable=True),
        sa.Column("actor_id",   sa.Integer(),     nullable=True),
        sa.Column("actor_name", sa.String(150),   nullable=True),
        sa.Column("action",     sa.String(50),    nullable=False),
        sa.Column("entity",     sa.String(50),    nullable=False),
        sa.Column("entity_id",  sa.String(100),   nullable=False),
        sa.Column("before_val", postgresql.JSONB(), nullable=True),
        sa.Column("after_val",  postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45),    nullable=True),
        sa.Column("user_agent", sa.String(300),   nullable=True),
        sa.Column("notes",      sa.Text(),        nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), index=True),
    )
    op.create_index("ix_audit_trail_entity",     "audit_trail", ["entity"])
    op.create_index("ix_audit_trail_action",     "audit_trail", ["action"])
    op.create_index("ix_audit_trail_store_id",   "audit_trail", ["store_id"])

    # ── t: sync_log table (BIGSERIAL id, JSONB for conflict) ─────────────────
    op.create_table(
        "sync_log",
        sa.Column("id",          sa.BigInteger(), primary_key=True),
        sa.Column("entity",      sa.String(50),   nullable=False),
        sa.Column("entity_id",   sa.String(100),  nullable=True),
        sa.Column("direction",   sa.String(20),   nullable=False),
        sa.Column("status",      sa.String(20),   nullable=False),
        sa.Column("records_in",  sa.Integer(),    default=0),
        sa.Column("records_out", sa.Integer(),    default=0),
        sa.Column("conflict",    postgresql.JSONB(), nullable=True),
        sa.Column("error_msg",   sa.Text(),       nullable=True),
        sa.Column("duration_ms", sa.Integer(),    nullable=True),
        sa.Column("checkpoint",  sa.String(50),   nullable=True),
        sa.Column("synced_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), index=True),
    )
    op.create_index("ix_sync_log_entity", "sync_log", ["entity"])
    op.create_index("ix_sync_log_status", "sync_log", ["status"])


def downgrade() -> None:
    # Drop new tables in reverse dependency order
    op.drop_table("sync_log")
    op.drop_table("audit_trail")
    op.drop_table("stock_movements")
    op.drop_table("suppliers")

    # Revert employees.pin
    op.execute("ALTER TABLE employees ALTER COLUMN pin TYPE VARCHAR(10) USING pin::VARCHAR(10)")

    # Revert transactions sync columns
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS synced_at")
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS sync_status")

    # Revert transactions money columns → FLOAT
    for col in ("change_given", "cash_tendered", "total", "vat_amount",
                "discount_amount", "subtotal"):
        op.execute(
            f"ALTER TABLE transactions ALTER COLUMN {col} TYPE FLOAT "
            f"USING {col}::FLOAT"
        )

    # Revert products money columns → FLOAT
    for col in ("cost_price", "selling_price"):
        op.execute(
            f"ALTER TABLE products ALTER COLUMN {col} TYPE FLOAT "
            f"USING {col}::FLOAT"
        )

    # Revert transaction_items money columns → FLOAT
    for col in ("line_total", "vat_amount", "discount", "cost_price_snap", "unit_price"):
        op.execute(
            f"ALTER TABLE transaction_items ALTER COLUMN {col} TYPE FLOAT "
            f"USING {col}::FLOAT"
        )
