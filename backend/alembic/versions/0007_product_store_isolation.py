"""Enforce product store isolation indexes and SKU uniqueness per store

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-20

What this migration does:
  1. Replaces the global UNIQUE constraint on products.sku with a
     per-store unique constraint (store_id, sku)
  2. Replaces the global UNIQUE constraint on products.barcode with
     per-store unique (store_id, barcode)
  3. Adds composite index (store_id, is_active) for product list queries
     which now always filter by both columns

Safety:
  - These changes WILL FAIL if your current data has duplicate SKUs across
    stores (they are all in store_id=NULL currently).
  - Run: UPDATE products SET store_id = 1 WHERE store_id IS NULL; first.
  - Then run this migration.
"""

from alembic import op
import sqlalchemy as sa

revision      = "0007"
down_revision = "0006"
branch_labels = None
depends_on    = None

# OPERATOR NOTE: Ensure all products have a store_id before running.
# UPDATE products SET store_id = 1 WHERE store_id IS NULL;


def upgrade() -> None:
    # 1. Drop the old global unique index on sku
    op.execute("DROP INDEX IF EXISTS ix_products_sku")
    try:
        op.drop_constraint("products_sku_key", "products", type_="unique")
    except Exception:
        op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_sku_key")

    # 2. Add per-store unique on (store_id, sku)
    op.create_unique_constraint(
        "uq_product_store_sku",
        "products",
        ["store_id", "sku"],
    )
    # Keep an index on sku alone for fast lookups
    op.create_index("ix_products_sku", "products", ["sku"])

    # 3. Drop old global unique on barcode, add per-store
    op.execute("DROP INDEX IF EXISTS ix_products_barcode")
    try:
        op.drop_constraint("products_barcode_key", "products", type_="unique")
    except Exception:
        op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_barcode_key")

    op.execute("""
        CREATE UNIQUE INDEX uq_product_store_barcode
        ON products (store_id, barcode)
        WHERE barcode IS NOT NULL
    """)
    op.create_index("ix_products_barcode", "products", ["barcode"])

    # 4. Composite index for product list queries (always filter store_id + is_active)
    op.create_index(
        "idx_product_store_active",
        "products",
        ["store_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("idx_product_store_active", table_name="products")
    op.execute("DROP INDEX IF EXISTS uq_product_store_barcode")
    op.create_unique_constraint("products_barcode_key", "products", ["barcode"])
    op.drop_constraint("uq_product_store_sku", "products", type_="unique")
    op.execute("DROP INDEX IF EXISTS ix_products_sku")
    op.create_unique_constraint("products_sku_key", "products", ["sku"])
