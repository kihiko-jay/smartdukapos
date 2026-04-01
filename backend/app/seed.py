"""
Seed the database with initial data.
Run once after first migration:  python -m app.seed

NEVER run this against a production database. Set APP_ENV=production in your
.env to make this file refuse to execute.
"""

import os
from app.database import SessionLocal
# Note: run `alembic upgrade head` before seeding to ensure schema is current.
from app.models.product import Product, Category
from app.models.employee import Employee, Role
from app.core.security import hash_password


CATEGORIES = ["Dairy", "Bakery", "Grocery", "Beverages", "Household"]

PRODUCTS = [
    {"sku": "MLK001", "barcode": "5000159407236", "name": "Brookside Milk 500ml",        "category": "Dairy",     "selling_price": 65,  "cost_price": 50,  "stock_quantity": 142, "reorder_level": 30},
    {"sku": "BRD002", "barcode": "6009695884193", "name": "Supa Loaf White Bread",        "category": "Bakery",    "selling_price": 55,  "cost_price": 40,  "stock_quantity": 38,  "reorder_level": 25},
    {"sku": "SUG003", "barcode": "6001068020048", "name": "Mumias Sugar 1kg",             "category": "Grocery",   "selling_price": 145, "cost_price": 110, "stock_quantity": 74,  "reorder_level": 20},
    {"sku": "OIL004", "barcode": "6001234567890", "name": "Elianto Cooking Oil 1L",       "category": "Grocery",   "selling_price": 215, "cost_price": 175, "stock_quantity": 29,  "reorder_level": 20},
    {"sku": "RCE005", "barcode": "6009876543210", "name": "Pishori Rice 2kg",             "category": "Grocery",   "selling_price": 380, "cost_price": 290, "stock_quantity": 55,  "reorder_level": 15},
    {"sku": "EGG006", "barcode": "6001111111111", "name": "Eggs (Tray 30)",               "category": "Dairy",     "selling_price": 520, "cost_price": 420, "stock_quantity": 4,   "reorder_level": 10},
    {"sku": "WSH007", "barcode": "5000101234567", "name": "Omo Washing Powder 1kg",       "category": "Household", "selling_price": 195, "cost_price": 150, "stock_quantity": 41,  "reorder_level": 15},
    {"sku": "TLT008", "barcode": "6002222222222", "name": "Softex Toilet Paper 10pk",     "category": "Household", "selling_price": 340, "cost_price": 260, "stock_quantity": 63,  "reorder_level": 20},
    {"sku": "TEA009", "barcode": "6003333333333", "name": "Ketepa Pride Tea 100g",        "category": "Beverages", "selling_price": 120, "cost_price": 90,  "stock_quantity": 87,  "reorder_level": 20},
    {"sku": "CKE010", "barcode": "5449000000996", "name": "Coke 500ml",                   "category": "Beverages", "selling_price": 80,  "cost_price": 58,  "stock_quantity": 200, "reorder_level": 50},
    {"sku": "MAZ011", "barcode": "6004444444444", "name": "Mazola Corn Oil 2L",           "category": "Grocery",   "selling_price": 490, "cost_price": 390, "stock_quantity": 7,   "reorder_level": 10},
    {"sku": "SPR012", "barcode": "5449000131065", "name": "Sprite 500ml",                 "category": "Beverages", "selling_price": 80,  "cost_price": 58,  "stock_quantity": 180, "reorder_level": 50},
]

EMPLOYEES = [
    {"full_name": "Admin User",    "email": "admin@dukapos.ke",  "password": "admin1234",   "role": Role.ADMIN,      "pin": "0000"},
    {"full_name": "James Mwangi",  "email": "james@dukapos.ke",  "password": "cashier1234", "role": Role.CASHIER,    "pin": "1111", "terminal_id": "T01"},
    {"full_name": "Grace Wanjiru", "email": "grace@dukapos.ke",  "password": "cashier1234", "role": Role.CASHIER,    "pin": "2222", "terminal_id": "T02"},
    {"full_name": "Peter Kamau",   "email": "peter@dukapos.ke",  "password": "super1234",   "role": Role.SUPERVISOR, "pin": "3333"},
]


def seed():
    # Hard guard: refuse to run in production environments
    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env == "production":
        raise RuntimeError(
            "seed.py was invoked in a production environment (APP_ENV=production). "
            "This would overwrite real data with demo credentials. Aborting."
        )
    # Ensure schema is up to date: run `alembic upgrade head` before this.
    db = SessionLocal()

    # Categories
    cat_map = {}
    for cat_name in CATEGORIES:
        existing = db.query(Category).filter(Category.name == cat_name).first()
        if not existing:
            cat = Category(name=cat_name)
            db.add(cat)
            db.flush()
            cat_map[cat_name] = cat.id
        else:
            cat_map[cat_name] = existing.id

    # Products
    for p in PRODUCTS:
        if not db.query(Product).filter(Product.sku == p["sku"]).first():
            db.add(Product(
                sku=p["sku"],
                barcode=p.get("barcode"),
                name=p["name"],
                category_id=cat_map.get(p["category"]),
                selling_price=p["selling_price"],
                cost_price=p.get("cost_price"),
                stock_quantity=p["stock_quantity"],
                reorder_level=p["reorder_level"],
            ))

    # Employees
    for e in EMPLOYEES:
        if not db.query(Employee).filter(Employee.email == e["email"]).first():
            db.add(Employee(
                full_name=e["full_name"],
                email=e["email"],
                password=hash_password(e["password"]),
                role=e["role"],
                pin=e.get("pin"),
                terminal_id=e.get("terminal_id"),
            ))

    db.commit()
    db.close()
    print("✅ Database seeded successfully.")
    print("\n📋 Login credentials:")
    for e in EMPLOYEES:
        print(f"   {e['role'].value:12s}  {e['email']:25s}  password: {e['password']}")


if __name__ == "__main__":
    seed()


def seed_store():
    """Call after seed() — creates demo store and links employees to it."""
    from app.models.subscription import Store, SubStatus, Plan
    from datetime import datetime, timezone, timedelta

    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env == "production":
        raise RuntimeError(
            "seed_store() was invoked in a production environment (APP_ENV=production). Aborting."
        )
    # Ensure schema is up to date: run `alembic upgrade head` before this.
    db = SessionLocal()

    store = db.query(Store).filter(Store.name == "Demo Duka Store").first()
    if not store:
        store = Store(
            name          = "Demo Duka Store",
            location      = "Nairobi, Kenya",
            kra_pin       = "P051234567R",
            plan          = Plan.FREE,
            sub_status    = SubStatus.TRIALING,
            trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14),
        )
        db.add(store)
        db.flush()

        # Link all seeded employees to this store
        for emp_data in EMPLOYEES:
            emp = db.query(Employee).filter(Employee.email == emp_data["email"]).first()
            if emp:
                emp.store_id = store.id

        db.commit()
        print(f"✅ Demo store created (ID={store.id}) — 14-day trial active.")
    else:
        print("ℹ️  Demo store already exists.")
    db.close()


if __name__ == "__main__":
    seed()
    seed_store()
