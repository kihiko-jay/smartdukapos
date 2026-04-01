# DukaPOS v4 — Upgrade Guide

---

## v4.0 — Full Store Isolation + Platform Owner + Precision Fixes

**Release date:** 2026-03-23  
**Upgrade from:** v3 (internal v2.4) → v4.0  
**Migration:** `0008_v4_release` (safe, zero-downtime)

### What's new in v4.0

This release consolidates all fixes from the v3 production build and officially
stamps the codebase as **DukaPOS v4**. Every component — backend API, Electron
frontend, sync agent — now reports version `4.0.0`.

#### 🔒 Critical: Full store isolation (all endpoints)

Previously, any authenticated user could read products, reports, and customer
data belonging to **other stores** on the same platform. This was a data-privacy
bug affecting multi-tenant deployments. All endpoints are now fully isolated:

| Router | Fix applied |
|--------|------------|
| `products.py` | `list_products`, `get_product`, `get_by_barcode`, stock history, categories, suppliers — all filtered by `current.store_id` |
| `reports.py` | Z-tape, weekly, VAT, top products, low stock — all scoped to the requesting store |
| `transactions.py` | Transaction list, summary, and void — scoped to store |
| `platform.py` | PLATFORM_OWNER bypass to view any store for support purposes |

#### 🏢 PLATFORM_OWNER role

A new role `platform_owner` allows the app developer to:
- View all stores and their subscription status via `GET /api/v1/platform/stores`
- Manually activate or suspend stores
- See aggregate platform metrics
- Access any store's reports for support — without being scoped to any shop

This role is never exposed to shop admins. It bypasses subscription gates and
store-scoping checks on all endpoints.

#### 💰 NUMERIC(12,2) on customer credit columns

`customers.credit_limit` and `customers.credit_balance` were stored as IEEE 754
`FLOAT`. This caused silent rounding errors (e.g. KES 1000.00 stored as
999.9999999). Both columns are now `NUMERIC(12,2)` — exact decimal arithmetic.
Migration `0006` handles the conversion with `USING credit_limit::NUMERIC(12,2)`.

#### 🏷️ Per-store SKU and barcode uniqueness

The old global `UNIQUE` constraints on `products.sku` and `products.barcode`
prevented two different stores from using the same SKU or barcode (e.g. both
stocking Coca-Cola with barcode `5449000000996`). Migration `0007` replaces
these with per-store unique constraints:

```sql
UNIQUE (store_id, sku)
UNIQUE (store_id, barcode) WHERE barcode IS NOT NULL
```

#### 📇 Customer store scoping

Customers now belong to a specific store via `customers.store_id`. The old
global `UNIQUE(phone)` constraint is replaced with `UNIQUE(store_id, phone)` —
the same customer phone can exist in two different shops.

#### 🔑 Per-store Redis cache keys

Product list, barcode, and detail cache keys now include `store_id`. Previously
Shop A's cached product list could be served to Shop B. Cache key format:
`products:list:{store_id}:...`, `products:barcode:{store_id}:{barcode}`.

#### 📊 Z-tape store name from DB

Z-tape and reports now show the **actual shop's name and location** from the
`stores` table, not the global `settings.STORE_NAME` environment variable.
Multi-store platforms will now see the correct shop branding on each report.

#### 🗃️ New migration: `0008_v4_release`

- Creates `db_version` metadata table — records which app version last ran
  migrations (useful for zero-downtime deployments and rollback audits).
- Adds `idx_txn_store_sync_status` partial index on `transactions(store_id, sync_status)`
  — accelerates sync agent outbox queries on larger datasets.
- Ensures `audit_trail.notes` column exists (idempotent backfill).

---

### Breaking changes in v4.0

| Change | Impact | Action required |
|--------|--------|-----------------|
| `customers.store_id` FK added | Existing customers have `store_id = NULL` | Backfill: `UPDATE customers SET store_id = 1 WHERE store_id IS NULL;` |
| Per-store `UNIQUE(store_id, sku)` | Migration fails if products lack `store_id` | Backfill: `UPDATE products SET store_id = 1 WHERE store_id IS NULL;` |
| Per-store `UNIQUE(store_id, barcode)` | Same as above | Same backfill as SKU |
| `PLATFORM_OWNER` enum value added | Requires `ALTER TYPE role` (handled by migration 0006) | Run `alembic upgrade head` |

---

### Migration steps for v4.0

**Step 1 — Backfill store_id (single-store deployments)**

```bash
# If you have one store (store_id = 1):
psql $DATABASE_URL -c "UPDATE products  SET store_id = 1 WHERE store_id IS NULL;"
psql $DATABASE_URL -c "UPDATE customers SET store_id = 1 WHERE store_id IS NULL;"
psql $DATABASE_URL -c "UPDATE transactions SET store_id = 1 WHERE store_id IS NULL;"
```

**Step 2 — Run all pending migrations**

```bash
# Docker (recommended)
docker compose -f backend/docker-compose.prod.yml run --rm api alembic upgrade head

# Or directly
cd backend && alembic upgrade head
```

This applies migrations `0006`, `0007`, and `0008` if not already run.

**Step 3 — Deploy**

```bash
export APP_VERSION=4.0.0
docker compose -f backend/docker-compose.prod.yml pull
docker compose -f backend/docker-compose.prod.yml up -d --no-deps api sync-agent
```

**Step 4 — Verify**

```bash
# Check reported version
curl http://localhost:8000/health
# Expected: {"status":"ok","app":"DukaPOS","version":"4.0.0"}

# Check migration recorded
psql $DATABASE_URL -c "SELECT * FROM db_version ORDER BY applied_at DESC LIMIT 5;"

# Check store isolation is active (should return only your store's products)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/products | jq '.total'
```

---

### Environment variables — no new additions in v4.0

All environment variables from v3 (v2.4) carry forward unchanged.
See the v2.3 section below for the complete reference.

---


### What's new

**Sync Agent — Docker-native, fully tested, dead-letter queue**
- Sync agent now ships with its own `sync-agent/Dockerfile` and is wired into
  both `docker-compose.yml` (dev) and `docker-compose.prod.yml` (production)
  as a first-class service. No more manual PM2 setup.
- Dead-letter queue added to SQLite: items that exceed 10 retry attempts are
  promoted to `dead_letter_queue` (not silently discarded). Query via:
  `GET /api/v1/sync/dead-letter`
- Full test suite added (`sync-agent/tests/sync-agent.test.js`) using Node's
  built-in `node:test` runner — zero extra dependencies. Covers retryQueue
  CRUD, dead-letter promotion, checkpoint persistence, transforms, and
  idempotency key hash stability.
- CI/CD builds, pushes, and Trivy-scans the sync agent Docker image alongside
  the API image on every push to `main`.
- Staging deploy now restarts `api` and `sync-agent` in one atomic step.

**Redis caching**
- Product list, barcode lookup, and product detail endpoints now cache in Redis.
- Barcode lookups (highest-frequency cashier endpoint): 10-minute TTL.
- Product list: 5-minute TTL, invalidated on every create / update / stock-adjust.
- Graceful no-op fallback: if `REDIS_URL` is unset or Redis is unreachable, all
  cache calls are skipped and the app hits the DB normally.

**Redis pub/sub WebSocket bridge**
- M-PESA payment confirmations now route through a Redis pub/sub channel
  (`dukapos:ws:events`). With 4 uvicorn workers, any worker can receive the
  Daraja callback and publish; every worker subscribes and delivers only to its
  locally-connected terminals. Eliminates the 75% silent-drop rate of
  in-process-only WebSocket delivery.
- Falls back to direct in-process send if Redis is unavailable (single-worker safe).

**Redis-backed distributed rate limiting**
- Login and API rate limiters use Redis sorted-set Lua scripts (atomic, multi-worker safe).
- Falls back to in-process sliding-window limiter if Redis is unavailable.
- New general `api_rate_limiter` dependency applied to high-traffic endpoints.

**Sentry error tracking**
- Set `SENTRY_DSN` to enable. FastAPI + SQLAlchemy integrations included.
- 10% performance tracing sample rate. No-op if DSN is empty.

**Prometheus metrics endpoint**
- `GET /metrics` now returns both a JSON snapshot and a Prometheus text-format
  block (under the `"prometheus"` key) for direct scraper ingestion.

**DB Migration 0005** _(required — run before deploying)_
- Adds `customers.updated_at` required for LWW sync conflict resolution.
  Backfills `updated_at = created_at` for all existing rows.
- Adds 5 composite performance indexes:

  | Index | Table | Columns | Used by |
  |---|---|---|---|
  | `idx_txn_store_date` | transactions | store_id, created_at | Daily/monthly reports |
  | `idx_txn_cashier_date` | transactions | cashier_id, created_at | Cashier performance |
  | `idx_txn_status_date` | transactions | status, created_at | Dashboard queries |
  | `idx_customer_phone` | customers | phone | Sync agent upserts (hot path) |
  | `idx_sm_product_date` | stock_movements | product_id, created_at | Stock history pagination |

**nginx reverse proxy**
- TLS 1.2/1.3 with OCSP stapling, HSTS (2-year preload), and modern cipher suite.
- M-PESA callback endpoint (`/api/v1/mpesa/callback`) restricted to Safaricom
  production IP ranges — all other IPs receive 403 at the network layer.
- Per-zone rate limiting: auth (10 req/min), API (300 req/min), payments (30 req/min).
- Security headers: `X-Frame-Options: DENY`, `X-Content-Type-Options`, CSP, Referrer-Policy.
- `/health/deep` and `/metrics` blocked from public access (internal RFC-1918 only).
- JSON access log format for structured log ingestion.

**CI/CD pipeline**
- 6 jobs: `lint` → `test` → `test-sync-agent` → `docker` → `deploy-staging` → `pr-summary`
- Both API and sync agent Docker images built, tagged, pushed to GHCR, and
  scanned with Trivy (CRITICAL + HIGH) on every push to `main`.
- Coverage uploaded to Codecov; test results published as PR comments.

**Ops tooling**
- `scripts/backup.sh` — pg_dump with timestamped filename, optional S3 upload.
- `scripts/restore.sh` — restore from local or S3 backup.
- `scripts/load_test.py` — Locust suite covering login storm, barcode scan,
  concurrent checkout flow, and M-PESA STK push.

---

### Breaking changes in v2.3

- **`alembic upgrade head` is required** before starting the API. Migration 0005
  adds `customers.updated_at` which the sync router reads on every ingest cycle.
- `REDIS_URL` is strongly recommended. Without it: rate limiting is per-worker
  only, WebSocket delivery is single-worker only, and product caching is disabled.
- `SENTRY_DSN` is optional but recommended for production.
- Sync agent must be re-deployed from the new Docker image (or re-run
  `npm install` if running outside Docker) — the `dead_letter_queue` SQLite
  table is created automatically on first startup.

---

### Migration steps for v2.3

**1. Run database migration**

```bash
# Docker (recommended)
docker compose -f backend/docker-compose.prod.yml run --rm api alembic upgrade head

# Or directly
cd backend && alembic upgrade head
```

**2. Set new environment variables in `backend/.env`**

```env
# Redis — enables caching, distributed rate limiting, WS pub/sub
REDIS_URL=redis://:your-redis-password@redis:6379/0

# Sentry — optional
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
```

**3. Deploy with Docker Compose**

```bash
docker compose -f backend/docker-compose.prod.yml pull
docker compose -f backend/docker-compose.prod.yml up -d --no-deps api sync-agent
docker compose -f backend/docker-compose.prod.yml ps
docker compose -f backend/docker-compose.prod.yml logs sync-agent --tail=30
```

**4. Configure sync agent (first deployment)**

```bash
cd sync-agent
cp .env.example .env
# Edit .env — set LOCAL_DB_*, CLOUD_API_URL, CLOUD_API_KEY, STORE_ID
```

**5. Configure nginx**

```bash
# Obtain TLS certificate
certbot --nginx -d yourdomain.com

# Or mount existing certs
mkdir -p backend/nginx/certs
cp fullchain.pem backend/nginx/certs/
cp privkey.pem   backend/nginx/certs/

# Update server_name in nginx.conf then:
docker compose -f backend/docker-compose.prod.yml restart nginx
```

**6. Verify**

```bash
# API health
curl https://yourdomain.com/health

# Deep health (DB + Redis + WS terminals)
curl http://localhost:8000/health/deep  # internal only

# Sync dead-letter queue (should be empty on fresh start)
curl -H "X-API-Key: $SYNC_AGENT_API_KEY" \
  https://yourdomain.com/api/v1/sync/dead-letter

# Run load test before cutting production traffic
cd backend && python scripts/load_test.py
```

---

### Environment variables — complete reference (v2.3)

**`backend/.env`**

```env
DATABASE_URL=postgresql://dukapos:password@db:5432/dukapos_db
SECRET_KEY=<openssl rand -hex 32>
APP_NAME=DukaPOS
STORE_NAME=Your Store Name
DEBUG=false

REDIS_URL=redis://:password@redis:6379/0

RATE_LIMIT_LOGIN_PER_MINUTE=10
RATE_LIMIT_API_PER_MINUTE=300

SYNC_AGENT_API_KEY=<openssl rand -hex 32>

MPESA_ENV=production
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=...
MPESA_PASSKEY=...
MPESA_CALLBACK_URL=https://yourdomain.com/api/v1/mpesa/callback
MPESA_WEBHOOK_SECRET=<openssl rand -hex 32>

ETIMS_URL=https://etims-api.kra.go.ke/etims-api
ETIMS_DEVICE_SERIAL=...
ETIMS_PIN=...

SENTRY_DSN=https://...@....ingest.sentry.io/...

WORKERS=4
LOG_LEVEL=info
```

**`sync-agent/.env`**

```env
LOCAL_DB_HOST=localhost
LOCAL_DB_PORT=5432
LOCAL_DB_NAME=dukapos_db
LOCAL_DB_USER=dukapos
LOCAL_DB_PASS=your-db-password

CLOUD_API_URL=https://api.yourdomain.com/api/v1
CLOUD_API_KEY=<same as SYNC_AGENT_API_KEY above>

STORE_ID=1
BATCH_SIZE=500
SYNC_INTERVAL_TRANSACTIONS=10
SYNC_INTERVAL_PRODUCTS=60
SYNC_INTERVAL_CUSTOMERS=60

LOG_LEVEL=info
LOG_FILE=logs/sync-agent.log
RETRY_QUEUE_PATH=data/retry_queue.db
```

---


## v2.2 — Performance & real-time

### New features
- M-PESA payment confirmation now arrives via WebSocket — cashiers see instant
  confirmation instead of waiting up to 60 seconds for polling.
- Stock history endpoint: GET /api/v1/products/{id}/stock-history
  Returns the full stock movement ledger per product with pagination.

### Performance improvements
- Z-tape and weekly summary reports now use SQL aggregation (no more full-table
  loads into Python memory).
- Sync agent uses exponential backoff on cloud failures (max 5 min delay with jitter).

### Developer notes
- WebSocket hub lives in backend/app/core/notifier.py
- To test WebSocket locally: open two terminals, run the backend, then:
  wscat -c ws://localhost:8000/ws/pos/T01
  In another terminal, trigger an M-PESA callback manually with curl.

## v2.1 — Security & infrastructure patch

### Breaking changes
- SYNC_AGENT_API_KEY is now REQUIRED. Sync endpoints return 503 if not set.
- Employee PINs must be re-set via POST /auth/set-pin after this upgrade
  (existing plaintext PINs will fail bcrypt verification).

### Migration steps (run in this order)
1. Set SYNC_AGENT_API_KEY in backend .env: `openssl rand -hex 32`
2. Run: `cd backend && alembic upgrade head`
3. Verify: `cd backend && pytest` — all tests must pass before deploying
4. Deploy backend
5. Have each cashier set their PIN via the POS quick-lock screen

### eTIMS setup
- Set ETIMS_URL=https://etims-sbx-api.kra.go.ke/etims-api for sandbox testing
- Set ETIMS_URL=https://etims-api.kra.go.ke/etims-api for production
- Obtain ETIMS_DEVICE_SERIAL and ETIMS_PIN from the KRA eTIMS portal

## What changed and why

### Backend (FastAPI)

#### 1. FLOAT → NUMERIC(12,2) on all money columns
**File:** `app/models/product.py`, `app/models/transaction.py`

All `selling_price`, `cost_price`, `total`, `subtotal`, `vat_amount`,
`unit_price`, `line_total` etc. now use `Numeric(12, 2)` instead of `Float`.

**Why:** Python `float` uses IEEE 754 binary arithmetic. KES 65.00 stored as
float can silently become 64.99999999 in calculations — catastrophic in
financial records. `Numeric(12,2)` is exact decimal arithmetic.

**Schemas:** All money fields now use `Decimal` (Python) not `float`.
The API serialises them as strings like `"65.00"` — the frontend uses
`parseMoney()` to convert them before display.

#### 2. StockMovement ledger (NEW)
**File:** `app/models/product.py` — `StockMovement` table

Every stock change is now recorded with:
- `movement_type`: sale | purchase | adjustment | write_off | void_restore | sync
- `qty_before` / `qty_after` snapshots
- `ref_id` linking to the transaction or PO
- `performed_by` employee FK

`product.stock_quantity` still exists as a fast read-cache, but the
`stock_movements` table is the authoritative source for reconciliation.

**New endpoints:**
- `GET /products/{id}/stock-history` — full movement log per product

#### 3. store_id on transactions (FIXED)
**File:** `app/models/transaction.py`

Transactions now carry `store_id` (FK to `stores`). Previously only
`terminal_id` (string) linked a sale to a branch — useless for aggregation.

The sync agent injects `store_id` from `process.env.STORE_ID` for
transactions created before this field existed.

#### 4. SyncStatus on transactions (NEW)
**File:** `app/models/transaction.py`

New `sync_status` column: `pending | synced | failed | local`

The sync agent queries `WHERE sync_status IN ('pending','failed')` rather
than relying on timestamps. This is the outbox pattern — safer than CDC
for high-value data.

**New endpoint:**
- `POST /transactions/sync/mark-synced` — called by sync agent after cloud confirm

#### 5. AuditTrail + SyncLog tables (NEW)
**File:** `app/models/audit.py`

`audit_trail` — append-only compliance log. Never UPDATE/DELETE.
`sync_log` — records every sync agent operation for observability.

**New endpoints:**
- `GET /audit/trail` — read audit log (manager+)
- `GET /audit/sync-log` — read sync log (manager+)

#### 6. Sync ingest endpoints (NEW)
**File:** `app/routers/sync.py`

Called by the sync agent, not the frontend. Protected by `X-API-Key` header.

- `POST /sync/products` — upsert products by SKU
- `POST /sync/customers` — upsert customers by phone
- `POST /sync/transactions` — upsert completed transactions by txn_number (local wins)
- `GET /sync/cloud-updates/products` — cloud pushes price changes down to terminals
- `POST /sync/log` — sync agent writes its operation log

#### 7. Supplier model (NEW)
**File:** `app/models/product.py` — `Supplier` table

Vendor master linked from products. Enables purchase order tracking and
B2B KRA eTIMS invoicing (customer KRA PIN field on supplier).

---

### Sync Agent (NEW — `sync-agent/`)

Standalone Node.js 20 service. Runs as background process (PM2 / Windows Service).

**Setup:**
```bash
cd sync-agent
cp .env.example .env
# edit .env with your DB and cloud API credentials
npm install
npm start           # foreground
npm run pm2:start   # background (production)
```

**How it works:**
- Products, customers: timestamp CDC (`updated_at > last_checkpoint`)
- Transactions: outbox pattern (`sync_status = 'pending'`)
- Cloud → local: pulls price/catalog updates every 5 minutes
- All errors logged to `logs/sync-agent.log` + cloud `sync_log` table
- Checkpoints persisted in `checkpoints.json` — crash-safe

**Safety guarantees:**
- Legacy DB connection uses `default_transaction_read_only=on` (except cloud→local writes)
- Idempotent upserts — re-posting the same data is always safe
- Checkpoint never advances until cloud confirms write

---

### Frontend (React/Electron)

#### 1. parseMoney() / fmtKES() helpers (NEW)
**File:** `src/api/client.js`

```js
export const parseMoney = (v) => parseFloat(v ?? 0);
export const fmtKES     = (v) => `KES ${parseMoney(v).toLocaleString("en-KE", { minimumFractionDigits: 2 })}`;
```

**All price displays now use these helpers** — never bare `p.selling_price`
directly in arithmetic since the API now returns Decimal strings.

#### 2. Offline mode improvements (POSTerminal.jsx)
- Explicit `isOnline` badge in top bar
- Queued transaction count badge
- Receipt shows `📶 SAVED OFFLINE — will sync when online` when offline
- Auto-syncs queue when connectivity returns

#### 3. BackOffice: two new tabs

**Sync Monitor tab** — real-time view of sync agent activity:
- Status cards: success / error / conflict / skipped counts
- Full sync_log table with entity, direction, records in/out, duration, checkpoint

**Audit Trail tab** — KRA compliance log viewer:
- Filter by entity (transaction / product / employee)
- Shows actor, action, before/after state JSON diffs

#### 4. Inventory: inline price editing + stock history
- Click any price in Inventory tab to edit inline (writes to cloud, syncs down)
- "HISTORY" button per product opens full `stock_movements` log

#### 5. Unsynced transactions warning
- Overview tab shows amber banner when `unsynced_count > 0`
- Links mentally to Sync Monitor tab

---

## Migration steps

### Step 1: Run Alembic migration (adds new columns/tables)

```bash
cd backend
alembic revision --autogenerate -m "v2_numeric_stock_ledger_audit"
alembic upgrade head
```

Key changes Alembic will generate:
- ALTER TABLE products: FLOAT → NUMERIC for price columns
- ALTER TABLE transaction_items: FLOAT → NUMERIC
- ALTER TABLE transactions: add store_id, sync_status, synced_at
- CREATE TABLE stock_movements
- CREATE TABLE audit_trail
- CREATE TABLE sync_log
- CREATE TABLE suppliers

### Step 2: Backfill store_id on existing transactions

```sql
-- Set store_id for all existing transactions (adjust store_id as needed)
UPDATE transactions SET store_id = 1 WHERE store_id IS NULL;
```

### Step 3: Backfill sync_status on existing transactions

```sql
-- Mark all completed transactions as pending sync (agent will pick them up)
UPDATE transactions
SET sync_status = 'pending'
WHERE status = 'completed' AND sync_status IS NULL;
```

### Step 4: Start sync agent

```bash
cd sync-agent
cp .env.example .env
# Fill in LOCAL_DB_* and CLOUD_API_URL and CLOUD_API_KEY
npm install
npm run pm2:start
```

### Step 5: Verify sync

Check the Sync Monitor tab in BackOffice after a few minutes.
You should see green "success" rows for products, transactions, customers.

---

## Environment variables — new additions

### Backend `.env`
```
SYNC_AGENT_API_KEY=a-long-random-secret-for-sync-agent
```

### Sync agent `.env`
```
LOCAL_DB_HOST=localhost
LOCAL_DB_PORT=5432
LOCAL_DB_NAME=dukapos_db
LOCAL_DB_USER=dukapos
LOCAL_DB_PASS=dukapos_pass
CLOUD_API_URL=https://api.yourdomain.com/api/v1
CLOUD_API_KEY=a-long-random-secret-for-sync-agent
STORE_ID=1
BATCH_SIZE=500
SYNC_INTERVAL_TRANSACTIONS=10
SYNC_INTERVAL_PRODUCTS=60
```
