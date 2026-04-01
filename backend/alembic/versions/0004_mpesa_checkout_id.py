"""Add mpesa_checkout_id for callback correlation

Revision ID: 0004
Revises: 0003
Create Date: 2025-01-04 00:00:00

Adds mpesa_checkout_id to transactions table.
This is the CheckoutRequestID returned by Safaricom on STK push initiation.
It is used to correlate callbacks back to the correct transaction when
the AccountReference lookup fails (edge case: txn_number changed).

Also adds a unique index so we can detect duplicate callbacks instantly.
"""

from alembic import op
import sqlalchemy as sa

revision      = "0004"
down_revision = "0003"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("mpesa_checkout_id", sa.String(100), nullable=True),
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_txn_mpesa_checkout_id
        ON transactions (mpesa_checkout_id)
        WHERE mpesa_checkout_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_txn_mpesa_checkout_id")
    op.drop_column("transactions", "mpesa_checkout_id")
