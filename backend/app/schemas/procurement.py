"""
Procurement schemas — request/response validation for PO, GRN, and invoice matching.
"""

from __future__ import annotations
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


# ── ProductPackaging ──────────────────────────────────────────────────────────

class PackagingCreate(BaseModel):
    purchase_unit_type: str
    units_per_purchase: int = Field(..., gt=0)
    label: Optional[str] = None
    is_default: bool = False


class PackagingOut(BaseModel):
    id: int
    product_id: int
    purchase_unit_type: str
    units_per_purchase: int
    label: Optional[str]
    is_default: bool

    class Config:
        from_attributes = True


# ── PurchaseOrder ─────────────────────────────────────────────────────────────

class POItemCreate(BaseModel):
    product_id: int
    ordered_qty_purchase: Decimal = Field(..., gt=0)
    purchase_unit_type: str = "unit"
    units_per_purchase: int = Field(1, gt=0)
    unit_cost: Decimal = Field(..., ge=0)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def compute_base_units(self) -> "POItemCreate":
        # Validation only — actual DB write happens in the service
        if self.ordered_qty_purchase <= 0:
            raise ValueError("ordered_qty_purchase must be > 0")
        return self


class POItemOut(BaseModel):
    id: int
    product_id: int
    product_name: Optional[str] = None
    product_sku: Optional[str] = None
    ordered_qty_purchase: Decimal
    purchase_unit_type: str
    units_per_purchase: int
    ordered_qty_base: int
    unit_cost: Decimal
    line_total: Decimal
    received_qty_base: int
    damaged_qty_base: int
    rejected_qty_base: int
    remaining_qty_base: int
    accepted_qty_base: int
    notes: Optional[str]

    class Config:
        from_attributes = True


class POCreate(BaseModel):
    supplier_id: int
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    currency: str = "KES"
    items: List[POItemCreate] = Field(..., min_length=1)


class POUpdate(BaseModel):
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    items: Optional[List[POItemCreate]] = None


class POOut(BaseModel):
    id: int
    store_id: int
    supplier_id: int
    supplier_name: Optional[str] = None
    po_number: str
    status: str
    order_date: date
    expected_date: Optional[date]
    notes: Optional[str]
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    created_by: int
    approved_by: Optional[int]
    approved_at: Optional[datetime]
    created_at: datetime
    items: List[POItemOut] = []

    class Config:
        from_attributes = True


class POSummary(BaseModel):
    id: int
    po_number: str
    supplier_name: Optional[str] = None
    status: str
    order_date: date
    expected_date: Optional[date]
    total_amount: Decimal
    created_at: datetime
    item_count: int = 0

    class Config:
        from_attributes = True


# ── GRN ───────────────────────────────────────────────────────────────────────

class GRNItemCreate(BaseModel):
    product_id: int
    purchase_order_item_id: Optional[int] = None
    received_qty_purchase: Decimal = Field(..., ge=0)
    purchase_unit_type: str = "unit"
    units_per_purchase: int = Field(1, gt=0)
    damaged_qty_base: int = Field(0, ge=0)
    rejected_qty_base: int = Field(0, ge=0)
    cost_per_base_unit: Decimal = Field(..., ge=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_qty_breakdown(self) -> "GRNItemCreate":
        base = int(self.received_qty_purchase * self.units_per_purchase)
        if self.damaged_qty_base + self.rejected_qty_base > base:
            raise ValueError(
                "damaged_qty_base + rejected_qty_base cannot exceed received base quantity"
            )
        return self


class GRNItemOut(BaseModel):
    id: int
    product_id: int
    product_name: Optional[str] = None
    product_sku: Optional[str] = None
    purchase_order_item_id: Optional[int]
    received_qty_purchase: Decimal
    purchase_unit_type: str
    units_per_purchase: int
    received_qty_base: int
    damaged_qty_base: int
    rejected_qty_base: int
    accepted_qty_base: int
    cost_per_base_unit: Decimal
    line_total: Decimal
    batch_number: Optional[str]
    expiry_date: Optional[date]
    notes: Optional[str]

    class Config:
        from_attributes = True


class GRNCreate(BaseModel):
    supplier_id: int
    purchase_order_id: Optional[int] = None
    received_date: Optional[date] = None
    supplier_invoice_number: Optional[str] = None
    supplier_delivery_note: Optional[str] = None
    notes: Optional[str] = None
    items: List[GRNItemCreate] = Field(..., min_length=1)


class GRNOut(BaseModel):
    id: int
    store_id: int
    supplier_id: int
    supplier_name: Optional[str] = None
    purchase_order_id: Optional[int]
    po_number: Optional[str] = None
    grn_number: str
    status: str
    received_date: date
    supplier_invoice_number: Optional[str]
    supplier_delivery_note: Optional[str]
    notes: Optional[str]
    received_by: int
    checked_by: Optional[int]
    posted_at: Optional[datetime]
    created_at: datetime
    items: List[GRNItemOut] = []

    class Config:
        from_attributes = True


class GRNSummary(BaseModel):
    id: int
    grn_number: str
    supplier_name: Optional[str] = None
    purchase_order_id: Optional[int]
    po_number: Optional[str] = None
    status: str
    received_date: date
    created_at: datetime
    item_count: int = 0

    class Config:
        from_attributes = True


# ── Invoice Match ─────────────────────────────────────────────────────────────

class InvoiceMatchCreate(BaseModel):
    supplier_id: int
    purchase_order_id: Optional[int] = None
    grn_id: Optional[int] = None
    invoice_number: str
    invoice_date: Optional[date] = None
    invoice_total: Decimal = Field(..., ge=0)
    discrepancy_notes: Optional[str] = None


class InvoiceMatchResolve(BaseModel):
    matched_status: str   # matched | disputed
    discrepancy_notes: Optional[str] = None


class InvoiceMatchOut(BaseModel):
    id: int
    store_id: int
    supplier_id: int
    supplier_name: Optional[str] = None
    purchase_order_id: Optional[int]
    po_number: Optional[str] = None
    grn_id: Optional[int]
    grn_number: Optional[str] = None
    invoice_number: str
    invoice_date: Optional[date]
    invoice_total: Decimal
    matched_status: str
    discrepancy_notes: Optional[str]
    variance_json: Optional[str]
    created_by: int
    resolved_by: Optional[int]
    resolved_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Reporting ─────────────────────────────────────────────────────────────────

class ProcurementReportFilters(BaseModel):
    supplier_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    status: Optional[str] = None
    skip: int = 0
    limit: int = Field(50, le=200)
