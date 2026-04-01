"""P1 fixes: transaction_items tax snapshot cols, Transaction.store_id NOT NULL,
SubPayment.amount Numeric

Revision ID: 0010_p1_fixes
Revises: 0009_store_id_not_null
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_p1_fixes"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fix 4: snapshot tax classification on each sale line ─────────────────
    # Allows eTIMS to submit the correct taxTyCd (B/Z/E) per item without
    # querying the products table after the fact.
    op.add_column(
        "transaction_items",
        sa.Column("tax_code", sa.String(10), nullable=True),
    )
    op.add_column(
        "transaction_items",
        sa.Column("vat_exempt", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    # ── Fix 8: align ORM nullable=False with the DB constraint ───────────────
    # Migration 0009 already set the DB column NOT NULL; this makes the ORM
    # match so SQLAlchemy generates correct DDL on schema diffing tools.
    op.alter_column(
        "transactions",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # ── Fix 9: SubPayment.amount Float → Numeric(12,2) ───────────────────────
    # Consistent with every other money column in the schema.
    op.alter_column(
        "sub_payments",
        "amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="amount::numeric(12,2)",
    )


def downgrade() -> None:
    op.alter_column(
        "sub_payments",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "store_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.drop_column("transaction_items", "vat_exempt")
    op.drop_column("transaction_items", "tax_code")
