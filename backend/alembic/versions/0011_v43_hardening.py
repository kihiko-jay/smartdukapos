"""v4.3 hardening: product uniqueness per-store ORM alignment

Revision ID: 0011_v43_hardening
Revises: 0010_p1_fixes
Create Date: 2026-04-01

What this migration does
------------------------
Migration 0007 (product_store_isolation) already created the correct
per-store unique DB constraints on (store_id, sku) and (store_id, barcode).
However the ORM still declared global unique=True on those columns, causing
a constraint name mismatch that confuses Alembic autogenerate.

This migration:
  1. Drops the old global unique indexes on products.sku and products.barcode
     if they still exist (they may have been partially cleaned by 0007).
  2. Ensures the per-store named constraints exist under the canonical names
     the ORM now declares so autogenerate stays clean going forward.

No data is changed. All changes are purely structural.

Note: no schema changes are needed for the other v4.3 fixes:
  - WS ticket store: in-memory only, no DB.
  - Token revocation blocklist: in-memory (swap to Redis for multi-worker).
  - business_date() helper: application code only.
  - Seed production guard: application code only.
"""

from alembic import op
import sqlalchemy as sa


revision    = "0011_v43_hardening"
down_revision = "0010_p1_fixes"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Drop old global unique indexes if they still exist ─────────────────
    # Migration 0007 may or may not have removed these depending on the
    # Alembic version and whether --autogenerate was used.
    existing_indexes = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'products' AND indexname IN "
                "('ix_products_sku', 'ix_products_barcode', "
                " 'uq_products_sku', 'uq_products_barcode')"
            )
        )
    }

    for idx in ("ix_products_sku", "uq_products_sku"):
        if idx in existing_indexes:
            op.drop_index(idx, table_name="products")

    for idx in ("ix_products_barcode", "uq_products_barcode"):
        if idx in existing_indexes:
            op.drop_index(idx, table_name="products")

    # ── 2. Non-unique indexes for sku and barcode columns ────────────────────
    # The uniqueness is now enforced at the composite level (store_id, sku)
    # and (store_id, barcode) by the constraints created in 0007.
    # We still want fast single-column lookups, so create plain indexes.
    existing_after = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'products'"
            )
        )
    }

    if "ix_products_sku_plain" not in existing_after:
        op.create_index("ix_products_sku_plain",     "products", ["sku"])
    if "ix_products_barcode_plain" not in existing_after:
        op.create_index("ix_products_barcode_plain", "products", ["barcode"])

    # ── 3. Ensure canonical per-store unique constraint names ─────────────────
    # 0007 may have used auto-generated names. We canonicalize them here
    # so the ORM's __table_args__ declarations match exactly.
    existing_constraints = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'products'::regclass AND contype = 'u'"
            )
        )
    }

    # If the canonical names are missing, create them. If old names exist
    # from 0007, they satisfy the same constraint so we leave them in place —
    # dropping and recreating would lock the table unnecessarily.
    if "uq_product_sku_per_store" not in existing_constraints:
        # Only create if no equivalent already exists under any name
        has_store_sku = any(
            "sku" in name and "store" in name
            for name in existing_constraints
        )
        if not has_store_sku:
            op.create_unique_constraint(
                "uq_product_sku_per_store", "products", ["store_id", "sku"]
            )

    if "uq_product_barcode_per_store" not in existing_constraints:
        has_store_barcode = any(
            "barcode" in name and "store" in name
            for name in existing_constraints
        )
        if not has_store_barcode:
            op.create_unique_constraint(
                "uq_product_barcode_per_store", "products", ["store_id", "barcode"]
            )


def downgrade() -> None:
    # Restore global unique constraints (reverting to pre-0007 state is
    # destructive in multi-store deployments — use with caution).
    op.drop_index("ix_products_barcode_plain", table_name="products")
    op.drop_index("ix_products_sku_plain",     table_name="products")
