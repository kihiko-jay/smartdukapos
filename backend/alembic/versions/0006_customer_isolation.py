"""Customer store isolation and money precision

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-20

What this migration does:
  1. Adds store_id FK to customers table — customers belong to a specific shop
  2. Drops the global UNIQUE constraint on phone — replaces with per-store unique
  3. Converts credit_limit and credit_balance from FLOAT to NUMERIC(12,2)
  4. Adds updated_at to customers (required for LWW sync conflict resolution)
  5. Adds PLATFORM_OWNER to the Role enum

Safety:
  - Existing customers get store_id=NULL (they were already unscoped)
  - Run the backfill SQL in the operator note below before enforcing NOT NULL
  - The unique constraint change is non-destructive (just changes scope)
"""

from alembic import op
import sqlalchemy as sa

revision      = "0006"
down_revision = "0005"
branch_labels = None
depends_on    = None

# OPERATOR NOTE: After running this migration, backfill store_id on existing
# customers. You need to decide which customers belong to which store.
# If you have only one store (store_id=1):
#   UPDATE customers SET store_id = 1 WHERE store_id IS NULL;
# Then you can optionally enforce NOT NULL:
#   ALTER TABLE customers ALTER COLUMN store_id SET NOT NULL;


def upgrade() -> None:
    # 1. Add store_id column to customers
    op.add_column(
        "customers",
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=True),
    )
    op.create_index("ix_customers_store_id", "customers", ["store_id"])

    # 2. Drop old global unique constraint on phone
    # (constraint name may vary — drop by column if name lookup fails)
    try:
        op.drop_constraint("customers_phone_key", "customers", type_="unique")
    except Exception:
        # PostgreSQL names it differently sometimes
        op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_phone_key")
        op.execute("DROP INDEX IF EXISTS ix_customers_phone")

    # 3. Add per-store unique constraint on (store_id, phone)
    op.create_unique_constraint(
        "uq_customer_store_phone",
        "customers",
        ["store_id", "phone"],
    )

    # 4. Convert credit columns from FLOAT to NUMERIC(12,2)
    op.execute(
        "ALTER TABLE customers ALTER COLUMN credit_limit "
        "TYPE NUMERIC(12,2) USING credit_limit::NUMERIC(12,2)"
    )
    op.execute(
        "ALTER TABLE customers ALTER COLUMN credit_balance "
        "TYPE NUMERIC(12,2) USING credit_balance::NUMERIC(12,2)"
    )

    # 5. Add updated_at for LWW sync
    op.add_column(
        "customers",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE customers SET updated_at = created_at WHERE updated_at IS NULL")

    # 6. Add PLATFORM_OWNER to Role enum
    # PostgreSQL enums require ALTER TYPE
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'platform_owner'")


def downgrade() -> None:
    # Remove PLATFORM_OWNER from enum (PostgreSQL cannot remove enum values easily)
    # Best approach: recreate the type without the value
    op.execute("""
        ALTER TABLE employees
        ALTER COLUMN role TYPE VARCHAR(20)
    """)
    op.execute("DROP TYPE IF EXISTS role")
    op.execute("""
        CREATE TYPE role AS ENUM ('cashier', 'supervisor', 'manager', 'admin')
    """)
    op.execute("""
        ALTER TABLE employees
        ALTER COLUMN role TYPE role USING role::role
    """)

    op.drop_column("customers", "updated_at")

    op.execute(
        "ALTER TABLE customers ALTER COLUMN credit_balance "
        "TYPE FLOAT USING credit_balance::FLOAT"
    )
    op.execute(
        "ALTER TABLE customers ALTER COLUMN credit_limit "
        "TYPE FLOAT USING credit_limit::FLOAT"
    )

    op.drop_constraint("uq_customer_store_phone", "customers", type_="unique")
    op.create_unique_constraint("customers_phone_key", "customers", ["phone"])
    op.drop_index("ix_customers_store_id", table_name="customers")
    op.drop_column("customers", "store_id")
