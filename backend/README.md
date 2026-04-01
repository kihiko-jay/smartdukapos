# DukaPOS v4 Backend API

Cloud-native POS & Retail Management System for the Kenyan market. (v4.0)  
Built with **FastAPI + PostgreSQL**. KRA eTIMS compliant. M-PESA integrated.

---

## Tech Stack

| Layer       | Technology                          |
|-------------|-------------------------------------|
| Framework   | FastAPI 0.111                       |
| Database    | PostgreSQL 16 + SQLAlchemy 2        |
| Auth        | JWT (python-jose + passlib/bcrypt)  |
| M-PESA      | Safaricom Daraja API (STK Push)     |
| Tax         | KRA eTIMS OSCU REST API             |
| Container   | Docker + Docker Compose             |

---

## Project Structure

```
dukapos/
├── app/
│   ├── core/
│   │   ├── config.py       # All settings via .env
│   │   ├── security.py     # JWT + bcrypt
│   │   └── deps.py         # FastAPI dependencies + role guards
│   ├── models/
│   │   ├── product.py      # Product + Category ORM models
│   │   ├── transaction.py  # Transaction + TransactionItem
│   │   ├── employee.py     # Employee + Role enum
│   │   └── customer.py     # Customer (loyalty + credit)
│   ├── schemas/
│   │   ├── product.py      # Pydantic request/response schemas
│   │   ├── transaction.py
│   │   └── auth.py
│   ├── routers/
│   │   ├── auth.py         # Login, clock-in/out, employee management
│   │   ├── products.py     # CRUD + barcode lookup + stock adjustment
│   │   ├── transactions.py # Create sale, void, M-PESA confirm
│   │   ├── reports.py      # Z-tape, weekly, VAT, top products, low stock
│   │   ├── mpesa.py        # STK push + Daraja callback handler
│   │   └── etims.py        # KRA invoice submission + retry
│   ├── services/
│   │   ├── mpesa.py        # Daraja API client
│   │   └── etims.py        # KRA eTIMS API client + QR generation
│   ├── database.py         # SQLAlchemy engine + session
│   ├── main.py             # FastAPI app + CORS + router registration
│   └── seed.py             # Initial data (products, employees)
├── .env.example
├── requirements.txt
└── docker-compose.yml
```

---

## Quick Start (Local Development)

### 1. Clone and set up environment

```bash
git clone <your-repo>
cd dukapos
cp .env.example .env
# Edit .env — set SECRET_KEY at minimum
```

### 2. Start PostgreSQL with Docker

```bash
docker-compose up db -d
```

Or if you have PostgreSQL installed locally, create the database:
```bash
createdb dukapos_db
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the API

```bash
uvicorn app.main:app --reload
```

### 5. Seed the database

```bash
python -m app.seed
```

### 6. Open the interactive docs

```
http://localhost:8000/docs
```

---

## Default Login Credentials (after seeding)

| Role       | Email                  | Password      |
|------------|------------------------|---------------|
| Admin      | admin@dukapos.ke       | admin1234     |
| Cashier    | james@dukapos.ke       | cashier1234   |
| Cashier    | grace@dukapos.ke       | cashier1234   |
| Supervisor | peter@dukapos.ke       | super1234     |

**Change all passwords before deploying!**

---

## API Endpoints

### Auth
```
POST   /api/v1/auth/login          # Get JWT token
POST   /api/v1/auth/employees      # Create employee (admin only)
POST   /api/v1/auth/clock-in       # Cashier clock in
POST   /api/v1/auth/clock-out      # Cashier clock out
GET    /api/v1/auth/me             # Current user info
```

### Products
```
GET    /api/v1/products            # List + search products
GET    /api/v1/products/{id}       # Get product by ID
GET    /api/v1/products/barcode/{barcode}  # Barcode lookup
POST   /api/v1/products            # Create product (manager)
PATCH  /api/v1/products/{id}       # Update product (manager)
POST   /api/v1/products/stock/adjust      # Adjust stock (manager)
GET    /api/v1/products/categories # List categories
```

### Transactions
```
POST   /api/v1/transactions        # Create sale (POS checkout)
GET    /api/v1/transactions        # List with filters
GET    /api/v1/transactions/summary/today  # Today's summary
GET    /api/v1/transactions/{id}   # Get single transaction
POST   /api/v1/transactions/{id}/void      # Void (manager)
POST   /api/v1/transactions/{id}/mpesa-confirm  # M-PESA callback
```

### Reports
```
GET    /api/v1/reports/z-tape      # End of day Z-tape
GET    /api/v1/reports/weekly      # Weekly sales summary
GET    /api/v1/reports/vat         # Monthly VAT report (KRA filing)
GET    /api/v1/reports/top-products
GET    /api/v1/reports/low-stock
```

### M-PESA
```
POST   /api/v1/mpesa/stk-push      # Trigger STK push
POST   /api/v1/mpesa/stk-query     # Poll payment status
POST   /api/v1/mpesa/callback      # Safaricom posts here (public URL needed)
```

### KRA eTIMS
```
POST   /api/v1/etims/submit/{txn_id}  # Submit invoice to KRA
GET    /api/v1/etims/pending          # List unsynced transactions
POST   /api/v1/etims/retry-all        # Bulk retry failed submissions
```

---

## M-PESA Setup (Daraja)

1. Register at https://developer.safaricom.co.ke
2. Create an app — get Consumer Key + Consumer Secret
3. For sandbox: use shortcode `174379`, passkey from developer portal
4. Set `MPESA_ENV=sandbox` in `.env` during development
5. For the callback URL during local dev, use **ngrok**:
   ```bash
   ngrok http 8000
   # Copy the https URL → set as MPESA_CALLBACK_URL in .env
   ```
6. Switch to `MPESA_ENV=production` and your live credentials before launch

---

## KRA eTIMS Setup

1. Register your business on iTax: https://itax.kra.go.ke
2. Apply for eTIMS onboarding through your KRA tax office
3. You'll receive: KRA PIN, Branch ID, Device Serial Number
4. Set these in `.env`: `ETIMS_PIN`, `ETIMS_BRANCH_ID`, `ETIMS_DEVICE_SERIAL`
5. For testing, KRA provides a sandbox environment

---

## Role Permissions

| Endpoint Type          | Cashier | Supervisor | Manager | Admin |
|------------------------|---------|------------|---------|-------|
| View products/prices   | ✅      | ✅         | ✅      | ✅    |
| Create transactions    | ✅      | ✅         | ✅      | ✅    |
| View transactions      | ✅      | ✅         | ✅      | ✅    |
| Void transactions      | ❌      | ✅         | ✅      | ✅    |
| Create/edit products   | ❌      | ❌         | ✅      | ✅    |
| Adjust stock           | ❌      | ❌         | ✅      | ✅    |
| View reports           | ❌      | ❌         | ✅      | ✅    |
| Create employees       | ❌      | ❌         | ❌      | ✅    |

---

## Connecting the React Frontend

In your POS terminal and back office React apps, set:
```javascript
const API_BASE = "http://localhost:8000/api/v1";

// Login
const res = await fetch(`${API_BASE}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
const { access_token } = await res.json();

// Authenticated request
const products = await fetch(`${API_BASE}/products`, {
  headers: { "Authorization": `Bearer ${access_token}` },
});
```

---

## Next Steps

- [ ] Connect React POS terminal to `/api/v1/products` and `/api/v1/transactions`
- [ ] Connect React back office to `/api/v1/reports` endpoints
- [ ] Get Safaricom Daraja sandbox credentials and test STK push
- [ ] Apply for KRA eTIMS onboarding
- [ ] Deploy to Railway or Render (free tier works for MVP)
- [ ] Set up ngrok for M-PESA callback testing
