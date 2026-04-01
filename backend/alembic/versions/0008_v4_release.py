"""DukaPOS v4.0 release migration — store isolation, precision, platform role

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-23

This migration is a v4.0 marker migration. Migrations 0006 and 0007 applied
the critical store-isolation and numeric precision fixes. This migration:

  1. Adds a db_version metadata table to record which app version last
     ran migrations — useful for zero-downtime deployments and rollback audits.
  2. Records the v4.0 release timestamp.
  3. Ensures the audit_trail table has the 'notes' column (added in some
     v2.x branches; idempotent if already present).
  4. Adds an index on transactions(store_id, sync_status) to accelerate
     the sync agent outbox query on larger datasets.

All destructive schema changes were handled in 0006 and 0007.
This migration is safe to run on a live database with zero downtime.

Prerequisites:
  - Migrations 0001 through 0007 must be applied first.
  - All products must have store_id set (UPDATE products SET store_id = 1 WHERE store_id IS NULL;)
  - All customers must have store_id set (UPDATE customers SET store_id = 1 WHERE store_id IS NULL;)
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision      = "0008"
down_revision = "0007"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # 1. Create db_version metadata table
    op.create_table(
        "db_version",
        sa.Column("id",          sa.Integer(), primary_key=True),
        sa.Column("app_version", sa.String(20), nullable=False),
        sa.Column("migration",   sa.String(10), nullable=False),
        sa.Column("applied_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("notes",       sa.Text(), nullable=True),
    )

    # 2. Record the v4.0 release
    op.execute(
        "INSERT INTO db_version (app_version, migration, notes) "
        "VALUES ('4.0.0', '0008', 'DukaPOS v4.0 release — full store isolation, "
        "NUMERIC precision, PLATFORM_OWNER role, per-store SKU/barcode uniqueness')"
    )

    # 3. Ensure audit_trail.notes column exists (idempotent)
    # Use a DO block so it does not fail if the column already exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='audit_trail' AND column_name='notes'
            ) THEN
                ALTER TABLE audit_trail ADD COLUMN notes TEXT;
            END IF;
        END $$;
    """)

    # 4. Composite index for sync agent outbox query
    # (store_id, sync_status) — agent queries WHERE sync_status IN ('pending','failed')
    # This index also speeds up per-store pending-count queries on the Overview tab.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_store_sync_status
        ON transactions (store_id, sync_status)
        WHERE sync_status IN ('pending', 'failed');
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_txn_store_sync_status")
    # Note: we do not drop audit_trail.notes — it is additive and safe to keep
    op.drop_table("db_version")
