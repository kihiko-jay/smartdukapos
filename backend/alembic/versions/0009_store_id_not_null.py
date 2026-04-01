"""Enforce store_id NOT NULL on categories, suppliers, and stock_movements

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-31

Background
----------
backend/app/models/product.py previously defined store_id as nullable=True on
three models — Category, Supplier, and StockMovement. The application layer
already filters every query by store_id correctly, but the database had no
constraint to back that up. A bug, a direct SQL insert, or a future migration
oversight could silently create orphaned rows with a NULL store_id that would
then bleed across tenant boundaries at query time.

This migration closes that gap by:
  1. Back-filling any existing NULL store_id rows to store 1 (the first store
     created during registration). See WARNING below before running on a live
     database.
  2. Setting the column NOT NULL on all three tables.

WARNING — read before running on a live database
-------------------------------------------------
The safety UPDATE in upgrade() assigns store_id = 1 to any row where
store_id IS NULL.  This is a best-effort fallback for databases that were
seeded incorrectly; it is NOT guaranteed to be the right store for every row.

Before running this migration on an existing database, operators MUST verify
there are no orphaned rows:

    SELECT COUNT(*) FROM categories     WHERE store_id IS NULL;
    SELECT COUNT(*) FROM suppliers      WHERE store_id IS NULL;
    SELECT COUNT(*) FROM stock_movements WHERE store_id IS NULL;

If the counts are all zero the migration is fully safe to run without any
manual intervention.

If any counts are non-zero, audit those rows first (e.g. SELECT * FROM
categories WHERE store_id IS NULL) and assign the correct store_id manually
before running the migration — or accept the store-1 fallback if that is
appropriate for your deployment.

Locking behaviour
-----------------
PostgreSQL 12+: ALTER COLUMN SET NOT NULL on a column that already has a CHECK
constraint added in the same transaction does NOT require a full table rewrite.
Alembic's op.alter_column(nullable=False) adds a NOT NULL constraint via
ALTER TABLE … ALTER COLUMN … SET NOT NULL, which takes only a brief
ACCESS EXCLUSIVE lock to update the catalog — equivalent in duration to
adding an index CONCURRENTLY on most table sizes.

On very large tables (millions of rows) consider running the safety UPDATE
manually during a maintenance window before deploying this migration so that
the UPDATE itself does not hold a lock while updating data.
"""

from alembic import op
import sqlalchemy as sa

# ── Revision identifiers ──────────────────────────────────────────────────────
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: back-fill any orphaned NULL rows ──────────────────────────────
    # This is a safety net only. Operators should verify row counts are zero
    # before running (see docstring above).
    op.execute("UPDATE categories      SET store_id = 1 WHERE store_id IS NULL")
    op.execute("UPDATE suppliers       SET store_id = 1 WHERE store_id IS NULL")
    op.execute("UPDATE stock_movements SET store_id = 1 WHERE store_id IS NULL")

    # ── Step 2: apply NOT NULL constraints ───────────────────────────────────
    op.alter_column(
        "categories",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "suppliers",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "stock_movements",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    # Reverse the NOT NULL constraints only.
    # The store_id values that were back-filled from NULL are not restored —
    # that data was not meaningful and there is no safe way to reconstruct it.
    op.alter_column(
        "stock_movements",
        "store_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "suppliers",
        "store_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "categories",
        "store_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
