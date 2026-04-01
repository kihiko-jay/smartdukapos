from pydantic import BaseModel, Field
from typing import Annotated
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


class CategoryBase(BaseModel):
    name:        str
    description: Optional[str] = None
    parent_id:   Optional[int] = None

class CategoryCreate(CategoryBase): pass
class CategoryOut(CategoryBase):
    id:         int
    store_id:   Optional[int] = None
    class Config: from_attributes = True


class SupplierBase(BaseModel):
    name:         str
    contact_name: Optional[str] = None
    phone:        Optional[str] = None
    email:        Optional[str] = None
    address:      Optional[str] = None
    kra_pin:      Optional[str] = None

class SupplierCreate(SupplierBase): pass
class SupplierOut(SupplierBase):
    id:        int
    is_active: bool
    class Config: from_attributes = True


class ProductBase(BaseModel):
    sku:            str
    barcode:        Optional[str]    = None
    name:           str
    description:    Optional[str]   = None
    category_id:    Optional[int]   = None
    supplier_id:    Optional[int]   = None
    selling_price:  Annotated[Decimal, Field(gt=0, decimal_places=2)]
    cost_price:     Optional[Annotated[Decimal, Field(decimal_places=2)]] = None
    vat_exempt:     bool             = False
    tax_code:       str              = "B"
    stock_quantity: int              = 0
    reorder_level:  int              = 10
    unit:           str              = "piece"
    image_url:      Optional[str]   = None


class ProductCreate(ProductBase): pass

class ProductUpdate(BaseModel):
    name:           Optional[str]    = None
    selling_price:  Optional[Decimal]= None
    cost_price:     Optional[Decimal]= None
    stock_quantity: Optional[int]   = None
    reorder_level:  Optional[int]   = None
    is_active:      Optional[bool]  = None
    barcode:        Optional[str]   = None
    category_id:    Optional[int]   = None
    supplier_id:    Optional[int]   = None
    tax_code:       Optional[str]   = None


class ProductOut(ProductBase):
    id:           int
    uuid:         str
    is_active:    bool
    is_low_stock: bool
    stock_value:  float
    category:     Optional[CategoryOut] = None
    supplier:     Optional[SupplierOut] = None
    created_at:   datetime
    updated_at:   Optional[datetime]
    class Config: from_attributes = True


class StockAdjustment(BaseModel):
    product_id:      int
    quantity_change: int
    reason:          str   # purchase_order | damaged | manual_count | return | write_off
    notes:           Optional[str] = None


class StockMovementOut(BaseModel):
    id:            int
    product_id:    int
    movement_type: str
    qty_delta:     int
    qty_before:    int
    qty_after:     int
    ref_id:        Optional[str]
    notes:         Optional[str]
    created_at:    datetime
    class Config: from_attributes = True
