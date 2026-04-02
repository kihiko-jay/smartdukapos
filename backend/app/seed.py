"""
Seed the database with initial data.
Run once after schema exists:  python -m app.seed

NEVER run this against a production database. Set APP_ENV=production in your
.env to make this file refuse to execute.
"""

import os
from datetime import datetime, timezone, timedelta

from app.database import SessionLocal
# Note: for this repo in Codespaces/local dev, schema may be created via:
#   from app.database import Base, engine
#   from app.models import *
#   Base.metadata.create_all(bind=engine)
from app.models.product import Product, Category
from app.models.employee import Employee, Role
from app.models.subscription import Store, SubStatus, Plan
from app.core.security import hash_password


CATEGORIES = ["Dairy", "Bakery", "Grocery", "Beverages", "Household"]

PRODUCTS = [
    {"sku": "MLK001", "barcode": "5000159407236","itemcode":"100101", "name": "Brookside Milk 500ml",        "category": "Dairy",     "selling_price": 65,  "cost_price": 50,  "stock_quantity": 142, "reorder_level": 30},
    {"sku": "BRD002", "barcode": "6009695884193","itemcode":"100102","name": "Supa Loaf White Bread",        "category": "Bakery",    "selling_price": 55,  "cost_price": 40,  "stock_quantity": 38,  "reorder_level": 25},
    {"sku": "SUG003", "barcode": "6001068020048","itemcode":"100103", "name": "Mumias Sugar 1kg",             "category": "Grocery",   "selling_price": 145, "cost_price": 110, "stock_quantity": 74,  "reorder_level": 20},
    {"sku": "OIL004", "barcode": "6001234567890","itemcode":"100104","name": "Elianto Cooking Oil 1L",       "category": "Grocery",   "selling_price": 215, "cost_price": 175, "stock_quantity": 29,  "reorder_level": 20},
    {"sku": "RCE005", "barcode": "6009876543210","itemcode":"100105","name": "Pishori Rice 2kg",             "category": "Grocery",   "selling_price": 380, "cost_price": 290, "stock_quantity": 55,  "reorder_level": 15},
    {"sku": "EGG006", "barcode": "6001111111111","itemcode":"100106","name": "Eggs (Tray 30)",               "category": "Dairy",     "selling_price": 520, "cost_price": 420, "stock_quantity": 4,   "reorder_level": 10},
    {"sku": "WSH007", "barcode": "5000101234567","itemcode":"100107","name": "Omo Washing Powder 1kg",       "category": "Household", "selling_price": 195, "cost_price": 150, "stock_quantity": 41,  "reorder_level": 15},
    {"sku": "TLT008", "barcode": "6002222222222","itemcode":"100108", "name": "Softex Toilet Paper 10pk",     "category": "Household", "selling_price": 340, "cost_price": 260, "stock_quantity": 63,  "reorder_level": 20},
    {"sku": "TEA009", "barcode": "6003333333333","itemcode":"100109", "name": "Ketepa Pride Tea 100g",        "category": "Beverages", "selling_price": 120, "cost_price": 90,  "stock_quantity": 87,  "reorder_level": 20},
    {"sku": "CKE010", "barcode": "5449000000996","itemcode":"100111","name": "Coke 500ml",                   "category": "Beverages", "selling_price": 80,  "cost_price": 58,  "stock_quantity": 200, "reorder_level": 50},
    {"sku": "MAZ011", "barcode": "6004444444444","itemcode":"100112", "name": "Mazola Corn Oil 2L",           "category": "Grocery",   "selling_price": 490, "cost_price": 390, "stock_quantity": 7,   "reorder_level": 10},
    {"sku": "SPR012", "barcode": "5449000131065","itemcode":"100113", "name": "Sprite 500ml",                 "category": "Beverages", "selling_price": 80,  "cost_price": 58,  "stock_quantity": 180, "reorder_level": 50},
]

EMPLOYEES = [
    {"full_name": "Admin User",    "email": "admin@dukapos.ke",  "password": "admin1234",   "role": Role.ADMIN,      "pin": "0000"},
    {"full_name": "James Mwangi",  "email": "james@dukapos.ke",  "password": "cashier1234", "role": Role.CASHIER,    "pin": "1111", "terminal_id": "T01"},
    {"full_name": "Grace Wanjiru", "email": "grace@dukapos.ke",  "password": "cashier1234", "role": Role.CASHIER,    "pin": "2222", "terminal_id": "T02"},
    {"full_name": "Peter Kamau",   "email": "peter@dukapos.ke",  "password": "super1234",   "role": Role.SUPERVISOR, "pin": "3333"},
]


def seed():
    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env == "production":
        raise RuntimeError(
            "seed.py was invoked in a production environment (APP_ENV=production). "
            "This would overwrite real data with demo credentials. Aborting."
        )

    db = SessionLocal()
    try:
        # Create or fetch demo store first
        store = db.query(Store).filter(Store.name == "Demo Duka Store").first()
        if not store:
            store = Store(
                name="Demo Duka Store",
                location="Nairobi, Kenya",
                kra_pin="P051234567R",
                plan=Plan.FREE,
                sub_status=SubStatus.TRIALING,
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
            )
            db.add(store)
            db.flush()

        # Categories
        cat_map = {}
        for cat_name in CATEGORIES:
            existing = (
                db.query(Category)
                .filter(Category.store_id == store.id, Category.name == cat_name)
                .first()
            )
            if not existing:
                cat = Category(store_id=store.id, name=cat_name)
                db.add(cat)
                db.flush()
                cat_map[cat_name] = cat.id
            else:
                cat_map[cat_name] = existing.id

        # Products
        for p in PRODUCTS:
            existing_product = (
                db.query(Product)
                .filter(Product.store_id == store.id, Product.sku == p["sku"])
                .first()
            )
            if not existing_product:
                db.add(
                    Product(
                        store_id=store.id,
                        sku=p["sku"],
                        barcode=p.get("barcode"),
                        itemcode=p.get("itemcode"),
                        name=p["name"],
                        category_id=cat_map.get(p["category"]),
                        selling_price=p["selling_price"],
                        cost_price=p.get("cost_price"),
                        stock_quantity=p["stock_quantity"],
                        reorder_level=p["reorder_level"],
                    )
                )

        # Employees
        for e in EMPLOYEES:
            existing_employee = db.query(Employee).filter(Employee.email == e["email"]).first()
            if not existing_employee:
                db.add(
                    Employee(
                        store_id=store.id,
                        full_name=e["full_name"],
                        email=e["email"],
                        password=hash_password(e["password"]),
                        role=e["role"],
                        pin=e.get("pin"),
                        terminal_id=e.get("terminal_id"),
                    )
                )
            else:
                if existing_employee.store_id is None:
                    existing_employee.store_id = store.id

        db.commit()

        print("✅ Database seeded successfully.")
        print(f"🏪 Demo store: {store.name} (ID={store.id})")
        print("\n📋 Login credentials:")
        for e in EMPLOYEES:
            print(f"   {e['role'].value:12s}  {e['email']:25s}  password: {e['password']}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()