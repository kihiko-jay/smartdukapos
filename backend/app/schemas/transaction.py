from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from app.models.transaction import PaymentMethod, TransactionStatus, SyncStatus


class TransactionItemIn(BaseModel):
    product_id: int
    qty:        int     = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0, decimal_places=2)
    discount:   Decimal = Decimal("0.00")


class TransactionItemOut(BaseModel):
    id:               int
    product_id:       int
    product_name:     str
    sku:              str
    qty:              int
    unit_price:       Decimal
    cost_price_snap:  Optional[Decimal]
    discount:         Decimal
    vat_amount:       Decimal
    line_total:       Decimal
    class Config: from_attributes = True


class TransactionCreate(BaseModel):
    terminal_id:     Optional[str]   = "T01"
    items:           List[TransactionItemIn]
    payment_method:  PaymentMethod
    discount_amount: Decimal          = Decimal("0.00")
    cash_tendered:   Optional[Decimal]= None
    mpesa_phone:     Optional[str]   = None
    customer_id:     Optional[int]   = None


class TransactionOut(BaseModel):
    id:               int
    uuid:             str
    txn_number:       str
    store_id:         Optional[int]
    terminal_id:      Optional[str]
    subtotal:         Decimal
    discount_amount:  Decimal
    vat_amount:       Decimal
    total:            Decimal
    payment_method:   PaymentMethod
    cash_tendered:    Optional[Decimal]
    change_given:     Optional[Decimal]
    mpesa_ref:        Optional[str]
    status:           TransactionStatus
    sync_status:      SyncStatus
    etims_invoice_no: Optional[str]
    etims_synced:     bool
    cashier_id:       Optional[int]
    customer_id:      Optional[int]
    items:            List[TransactionItemOut] = []
    created_at:       datetime
    completed_at:     Optional[datetime]
    class Config: from_attributes = True


class TransactionSummary(BaseModel):
    id:             int
    txn_number:     str
    total:          Decimal
    payment_method: PaymentMethod
    status:         TransactionStatus
    sync_status:    SyncStatus
    cashier_id:     Optional[int]
    created_at:     datetime
    class Config: from_attributes = True
