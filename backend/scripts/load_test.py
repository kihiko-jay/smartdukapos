"""
DukaPOS — Load Testing Script (Locust)
Step 7.3: Simulate concurrent POS usage

Scenarios:
  1. CashierUser  — typical POS cashier: barcode scan → create transaction (80%)
  2. ManagerUser  — back-office manager: reports, product management (15%)
  3. SyncAgent    — sync agent: batch product/transaction upserts (5%)

Usage:
  # Install: pip install locust
  # Run headless (CI):
  locust -f scripts/load_test.py \
    --headless --users 50 --spawn-rate 5 \
    --run-time 120s \
    --host http://localhost:8000 \
    --csv=load_test_results

  # Run with web UI:
  locust -f scripts/load_test.py --host http://localhost:8000

Environment:
  LOAD_TEST_EMAIL     admin@teststore.com
  LOAD_TEST_PASSWORD  testpass123
  LOAD_TEST_SYNC_KEY  your-sync-agent-api-key
"""

import os
import random
import uuid
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner


# ── Config ────────────────────────────────────────────────────────────────────

LOGIN_EMAIL    = os.getenv("LOAD_TEST_EMAIL",    "admin@teststore.com")
LOGIN_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "testpass123")
SYNC_KEY       = os.getenv("LOAD_TEST_SYNC_KEY", "test-sync-key")

# Shared across all users — set once during on_start
_ACCESS_TOKEN  = None
_PRODUCT_IDS   = []
_PRODUCT_SKUS  = []


# ── Base user ─────────────────────────────────────────────────────────────────

class DukaPOSUser(HttpUser):
    """Base class: login and set auth headers."""

    abstract = True

    def on_start(self):
        resp = self.client.post("/api/v1/auth/login", json={
            "email":    LOGIN_EMAIL,
            "password": LOGIN_PASSWORD,
        }, name="auth/login [setup]")

        if resp.status_code != 200:
            self.environment.runner.quit()
            raise RuntimeError(f"Login failed: {resp.text}")

        token = resp.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        self._load_products()

    def _load_products(self):
        resp = self.client.get("/api/v1/products?limit=50",
                               headers=self.headers,
                               name="products [setup]")
        if resp.status_code == 200:
            products = resp.json()
            self._product_ids  = [p["id"]  for p in products if p.get("id")]
            self._product_skus = [p["sku"] for p in products if p.get("sku") and p.get("barcode")]
        else:
            self._product_ids  = []
            self._product_skus = []


# ── Cashier user (80% of load) ────────────────────────────────────────────────

class CashierUser(DukaPOSUser):
    """
    Simulates a POS cashier during a busy trading day.
    Mix: 50% product lookups, 50% transactions.
    """

    weight      = 8
    wait_time   = between(0.5, 3.0)   # cashier processes a sale every 0.5–3s

    @task(3)
    def lookup_product_by_barcode(self):
        """Barcode scan — most common cashier action."""
        if not self._product_skus:
            self.lookup_all_products()
            return
        sku = random.choice(self._product_skus)
        self.client.get(f"/api/v1/products/barcode/{sku}",
                        headers=self.headers,
                        name="products/barcode/[sku]")

    @task(3)
    def lookup_all_products(self):
        """Product grid load (POS startup or category browse)."""
        self.client.get("/api/v1/products?limit=100",
                        headers=self.headers,
                        name="products [list]")

    @task(4)
    def create_cash_transaction(self):
        """Complete a cash sale — the highest-frequency write operation."""
        if not self._product_ids:
            return

        product_id  = random.choice(self._product_ids)
        qty         = random.randint(1, 5)
        unit_price  = round(random.uniform(50, 500), 2)
        total_est   = unit_price * qty * 1.16
        cash        = round(total_est + random.randint(0, 100), 2)

        idem_key = f"LOAD-{uuid.uuid4().hex}"

        self.client.post(
            "/api/v1/transactions",
            headers={**self.headers, "Idempotency-Key": idem_key},
            json={
                "items": [{
                    "product_id": product_id,
                    "qty":        qty,
                    "unit_price": str(unit_price),
                    "discount":   "0.00",
                }],
                "discount_amount": "0.00",
                "payment_method":  "cash",
                "cash_tendered":   str(cash),
                "terminal_id":     f"T{random.randint(1,5):02d}",
            },
            name="transactions [create cash]",
        )

    @task(1)
    def check_health(self):
        """Occasional shallow health check (mimics monitoring agent)."""
        self.client.get("/health", name="health [shallow]")


# ── Manager user (15% of load) ────────────────────────────────────────────────

class ManagerUser(DukaPOSUser):
    """
    Simulates a store manager checking reports and managing inventory.
    Lower frequency — managers don't ring up sales.
    """

    weight    = 2
    wait_time = between(5, 30)

    @task(3)
    def daily_sales_report(self):
        self.client.get("/api/v1/reports/daily",
                        headers=self.headers,
                        name="reports/daily")

    @task(2)
    def transaction_history(self):
        self.client.get("/api/v1/transactions?limit=50",
                        headers=self.headers,
                        name="transactions [list]")

    @task(2)
    def low_stock_check(self):
        self.client.get("/api/v1/products?low_stock=true",
                        headers=self.headers,
                        name="products [low_stock]")

    @task(1)
    def token_refresh(self):
        """Simulate token refresh (access token expiry during long shift)."""
        resp = self.client.post("/api/v1/auth/login", json={
            "email":    LOGIN_EMAIL,
            "password": LOGIN_PASSWORD,
        }, name="auth/login [token refresh]")
        if resp.status_code == 200:
            token = resp.json()["access_token"]
            self.headers = {"Authorization": f"Bearer {token}"}


# ── Sync agent (5% of load) ───────────────────────────────────────────────────

class SyncAgentUser(DukaPOSUser):
    """
    Simulates the sync agent pushing batches to the cloud backend.
    Very low frequency — one batch every 30–120 seconds.
    """

    weight    = 1
    wait_time = between(30, 120)

    def on_start(self):
        # Sync agent uses API key auth, not JWT
        self.headers = {"X-Api-Key": SYNC_KEY}
        self._product_ids  = []
        self._product_skus = []

    @task(2)
    def sync_products(self):
        products = [
            {
                "sku":           f"LOAD-PROD-{i:04d}",
                "name":          f"Load Test Product {i}",
                "selling_price": str(round(random.uniform(50, 500), 2)),
                "stock_quantity": random.randint(0, 200),
                "is_active":     True,
            }
            for i in range(random.randint(5, 50))
        ]
        self.client.post(
            "/api/v1/sync/products",
            headers=self.headers,
            json={"records": products, "store_id": 1},
            name="sync/products [batch]",
        )

    @task(3)
    def sync_transactions(self):
        txns = [
            {
                "txn_number":     f"LOAD-TXN-{uuid.uuid4().hex[:8].upper()}",
                "subtotal":       str(round(random.uniform(100, 2000), 2)),
                "vat_amount":     "0.00",
                "total":          str(round(random.uniform(116, 2320), 2)),
                "payment_method": random.choice(["cash", "mpesa"]),
                "status":         "completed",
                "items":          [],
            }
            for _ in range(random.randint(5, 100))
        ]
        self.client.post(
            "/api/v1/sync/transactions",
            headers={**self.headers, "X-Idempotency-Key": uuid.uuid4().hex},
            json={"records": txns, "store_id": 1},
            name="sync/transactions [batch]",
        )


# ── Event hooks ───────────────────────────────────────────────────────────────

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print a summary on test completion."""
    stats = environment.stats.total
    print("\n" + "="*60)
    print("DukaPOS Load Test Summary")
    print("="*60)
    print(f"Total requests:  {stats.num_requests}")
    print(f"Failures:        {stats.num_failures}")
    print(f"Failure rate:    {stats.fail_ratio*100:.1f}%")
    print(f"Median RT:       {stats.median_response_time}ms")
    print(f"P95 RT:          {stats.get_response_time_percentile(0.95)}ms")
    print(f"P99 RT:          {stats.get_response_time_percentile(0.99)}ms")
    print(f"RPS:             {stats.current_rps:.1f}")
    print("="*60)

    if stats.fail_ratio > 0.01:   # >1% failure rate
        print("⚠️  WARNING: Failure rate exceeds 1% threshold!")
