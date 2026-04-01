"""Procurement module: product_packaging, purchase_orders, goods_received_notes,
supplier_invoice_matches

Revision ID: 0012_procurement
Revises: 0011_v43_hardening
Create Date: 2026-04-01

New tables
----------
  product_packaging        — per-product purchase unit definitions
  purchase_orders          — inbound purchase orders
  purchase_order_items     — line items on a PO
  goods_received_notes     — receiving events (GRN)
  goods_received_items     — line items on a GRN
  supplier_invoice_matches — invoice-to-PO/GRN matching and variance tracking

No existing tables are modified. All FKs reference pre-existing tables.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0012_procurement"
down_revision = "0011_v43_hardening"
branch_labels = None
depends_on    = None


def upgrade() -> None:

    # ── product_packaging ────────────────────────────────────────────────────
    op.create_table(
        "product_packaging",
        sa.Column("id",                 sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id",         sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("store_id",           sa.Integer(), sa.ForeignKey("stores.id",   ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_unit_type", sa.String(20),  nullable=False),
        sa.Column("units_per_purchase", sa.Integer(),   nullable=False, server_default="1"),
        sa.Column("label",              sa.String(100), nullable=True),
        sa.Column("is_default",         sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.Column("created_at",         sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("product_id", "purchase_unit_type", name="uq_packaging_product_unit"),
        sa.CheckConstraint("units_per_purchase > 0", name="ck_packaging_units_positive"),
    )
    op.create_index("ix_product_packaging_product_id", "product_packaging", ["product_id"])
    op.create_index("ix_product_packaging_store_id",   "product_packaging", ["store_id"])

    # ── purchase_orders ──────────────────────────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id",            sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("store_id",      sa.Integer(),     sa.ForeignKey("stores.id"),    nullable=False),
        sa.Column("supplier_id",   sa.Integer(),     sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("po_number",     sa.String(30),    nullable=False, unique=True),
        sa.Column("status",        sa.String(25),    nullable=False, server_default="draft"),
        sa.Column("order_date",    sa.Date(),        nullable=False, server_default=sa.func.current_date()),
        sa.Column("expected_date", sa.Date(),        nullable=True),
        sa.Column("notes",         sa.Text(),        nullable=True),
        sa.Column("currency",      sa.String(3),     nullable=False, server_default="KES"),
        sa.Column("subtotal",      sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("tax_amount",    sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("total_amount",  sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("created_by",    sa.Integer(),     sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("approved_by",   sa.Integer(),     sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("approved_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",    sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_purchase_orders_store_id",    "purchase_orders", ["store_id"])
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_status",      "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_created_at",  "purchase_orders", ["created_at"])
    op.create_index("ix_purchase_orders_po_number",   "purchase_orders", ["po_number"])

    # ── purchase_order_items ─────────────────────────────────────────────────
    op.create_table(
        "purchase_order_items",
        sa.Column("id",                   sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("purchase_order_id",    sa.Integer(),     sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id",           sa.Integer(),     sa.ForeignKey("products.id"), nullable=False),
        sa.Column("ordered_qty_purchase", sa.Numeric(10,3), nullable=False),
        sa.Column("purchase_unit_type",   sa.String(20),    nullable=False, server_default="unit"),
        sa.Column("units_per_purchase",   sa.Integer(),     nullable=False, server_default="1"),
        sa.Column("ordered_qty_base",     sa.Integer(),     nullable=False),
        sa.Column("unit_cost",            sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("line_total",           sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("received_qty_base",    sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("damaged_qty_base",     sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("rejected_qty_base",    sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("notes",                sa.Text(),        nullable=True),
        sa.CheckConstraint("ordered_qty_purchase > 0",  name="ck_poi_ordered_qty_positive"),
        sa.CheckConstraint("units_per_purchase > 0",     name="ck_poi_units_per_purchase_positive"),
        sa.CheckConstraint("unit_cost >= 0",             name="ck_poi_unit_cost_non_negative"),
    )
    op.create_index("ix_po_items_purchase_order_id", "purchase_order_items", ["purchase_order_id"])

    # ── goods_received_notes ─────────────────────────────────────────────────
    op.create_table(
        "goods_received_notes",
        sa.Column("id",                      sa.Integer(),  primary_key=True, autoincrement=True),
        sa.Column("store_id",                sa.Integer(),  sa.ForeignKey("stores.id"),          nullable=False),
        sa.Column("supplier_id",             sa.Integer(),  sa.ForeignKey("suppliers.id"),        nullable=False),
        sa.Column("purchase_order_id",       sa.Integer(),  sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("grn_number",              sa.String(30), nullable=False, unique=True),
        sa.Column("status",                  sa.String(20), nullable=False, server_default="draft"),
        sa.Column("received_date",           sa.Date(),     nullable=False, server_default=sa.func.current_date()),
        sa.Column("supplier_invoice_number", sa.String(100), nullable=True),
        sa.Column("supplier_delivery_note",  sa.String(100), nullable=True),
        sa.Column("notes",                   sa.Text(),     nullable=True),
        sa.Column("received_by",             sa.Integer(),  sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("checked_by",              sa.Integer(),  sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("posted_at",               sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",              sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",              sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_grn_store_id",    "goods_received_notes", ["store_id"])
    op.create_index("ix_grn_supplier_id", "goods_received_notes", ["supplier_id"])
    op.create_index("ix_grn_po_id",       "goods_received_notes", ["purchase_order_id"])
    op.create_index("ix_grn_status",      "goods_received_notes", ["status"])
    op.create_index("ix_grn_created_at",  "goods_received_notes", ["created_at"])
    op.create_index("ix_grn_number",      "goods_received_notes", ["grn_number"])

    # ── goods_received_items ─────────────────────────────────────────────────
    op.create_table(
        "goods_received_items",
        sa.Column("id",                     sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("grn_id",                 sa.Integer(),     sa.ForeignKey("goods_received_notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id",             sa.Integer(),     sa.ForeignKey("products.id"), nullable=False),
        sa.Column("purchase_order_item_id", sa.Integer(),     sa.ForeignKey("purchase_order_items.id"), nullable=True),
        sa.Column("received_qty_purchase",  sa.Numeric(10,3), nullable=False),
        sa.Column("purchase_unit_type",     sa.String(20),    nullable=False, server_default="unit"),
        sa.Column("units_per_purchase",     sa.Integer(),     nullable=False, server_default="1"),
        sa.Column("received_qty_base",      sa.Integer(),     nullable=False),
        sa.Column("damaged_qty_base",       sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("rejected_qty_base",      sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("cost_per_base_unit",     sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("line_total",             sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("batch_number",           sa.String(100),   nullable=True),
        sa.Column("expiry_date",            sa.Date(),        nullable=True),
        sa.Column("notes",                  sa.Text(),        nullable=True),
        sa.CheckConstraint("received_qty_purchase >= 0", name="ck_gri_received_qty_non_negative"),
        sa.CheckConstraint("damaged_qty_base >= 0",       name="ck_gri_damaged_qty_non_negative"),
        sa.CheckConstraint("rejected_qty_base >= 0",      name="ck_gri_rejected_qty_non_negative"),
        sa.CheckConstraint("units_per_purchase > 0",      name="ck_gri_units_per_purchase_positive"),
        sa.CheckConstraint("cost_per_base_unit >= 0",     name="ck_gri_cost_non_negative"),
    )
    op.create_index("ix_gri_grn_id",     "goods_received_items", ["grn_id"])
    op.create_index("ix_gri_product_id", "goods_received_items", ["product_id"])

    # ── supplier_invoice_matches ─────────────────────────────────────────────
    op.create_table(
        "supplier_invoice_matches",
        sa.Column("id",                 sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("store_id",           sa.Integer(),     sa.ForeignKey("stores.id"),               nullable=False),
        sa.Column("supplier_id",        sa.Integer(),     sa.ForeignKey("suppliers.id"),             nullable=False),
        sa.Column("purchase_order_id",  sa.Integer(),     sa.ForeignKey("purchase_orders.id"),       nullable=True),
        sa.Column("grn_id",             sa.Integer(),     sa.ForeignKey("goods_received_notes.id"),  nullable=True),
        sa.Column("invoice_number",     sa.String(100),   nullable=False),
        sa.Column("invoice_date",       sa.Date(),        nullable=True),
        sa.Column("invoice_total",      sa.Numeric(12,2), nullable=False, server_default="0"),
        sa.Column("matched_status",     sa.String(20),    nullable=False, server_default="unmatched"),
        sa.Column("discrepancy_notes",  sa.Text(),        nullable=True),
        sa.Column("variance_json",      sa.Text(),        nullable=True),
        sa.Column("created_by",         sa.Integer(),     sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("resolved_by",        sa.Integer(),     sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("resolved_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",         sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",         sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sim_store_id",       "supplier_invoice_matches", ["store_id"])
    op.create_index("ix_sim_supplier_id",    "supplier_invoice_matches", ["supplier_id"])
    op.create_index("ix_sim_matched_status", "supplier_invoice_matches", ["matched_status"])
    op.create_index("ix_sim_created_at",     "supplier_invoice_matches", ["created_at"])


def downgrade() -> None:
    op.drop_table("supplier_invoice_matches")
    op.drop_table("goods_received_items")
    op.drop_table("goods_received_notes")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("product_packaging")
