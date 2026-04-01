# OPERATOR NOTE: review the default store_id=1
# below before running on a multi-store deployment.
# If you have more than one store, update the WHERE clauses or run
# targeted UPDATE statements per store before executing this migration.

"""Backfill and enforce store_id NOT NULL on transactions and products

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-01 00:00:01.000000

WHY: store_id was added as nullable in the initial schema. Multi-branch
aggregation queries (reports, sync) break silently when store_id is NULL.
This migration backfills any NULL rows to store_id=1 (the default store)
and then enforces NOT NULL at the DB level.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill NULLs to the default store. Review the store_id value above
    # before running on a multi-store deployment.
    op.execute("UPDATE transactions SET store_id = 1 WHERE store_id IS NULL")
    op.execute("UPDATE products SET store_id = 1 WHERE store_id IS NULL")

    # Now enforce NOT NULL — will fail fast if backfill missed any rows
    op.execute("ALTER TABLE transactions ALTER COLUMN store_id SET NOT NULL")
    op.execute("ALTER TABLE products ALTER COLUMN store_id SET NOT NULL")


def downgrade() -> None:
    # Re-allow NULLs — does NOT delete the backfilled data
    op.execute("ALTER TABLE transactions ALTER COLUMN store_id DROP NOT NULL")
    op.execute("ALTER TABLE products ALTER COLUMN store_id DROP NOT NULL")
