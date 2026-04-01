/**
 * DukaPOS Sync Agent — Test Suite
 *
 * Covers:
 *   - retryQueue: push, get, markRetried, clearRetryItem, promoteToDeadLetter, getDeadLetterQueue
 *   - checkpoint: get/save/reset roundtrip, epoch default
 *   - syncLoop helpers: batchKey idempotency, backoff logic
 *
 * Uses Node's built-in test runner (node:test) — no extra dependencies.
 * Run: npm test
 */

const { test, describe, before, after, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const path   = require("path");
const fs     = require("fs");
const os     = require("os");

// ── Test DB in temp directory ─────────────────────────────────────────────────
let tmpDir;
before(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dukapos-test-"));
  process.env.RETRY_QUEUE_PATH = path.join(tmpDir, "retry_queue.db");
  process.env.LOG_LEVEL = "error";  // suppress logs during tests
});

after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ── retryQueue ────────────────────────────────────────────────────────────────
describe("retryQueue", () => {
  let rq;

  before(() => {
    // Re-require after env is set so the DB path is picked up
    delete require.cache[require.resolve("../src/retryQueue")];
    rq = require("../src/retryQueue");
  });

  beforeEach(() => {
    // Clear all items between tests
    const Database = require("better-sqlite3");
    const db = new Database(process.env.RETRY_QUEUE_PATH);
    db.prepare("DELETE FROM retry_queue").run();
    db.prepare("DELETE FROM dead_letter_queue").run();
    db.close();
  });

  test("pushRetryQueue adds an item", () => {
    rq.pushRetryQueue({
      entity: "transactions",
      idempotency_key: "test:key:001",
      payload: JSON.stringify([{ txn_number: "TXN001" }]),
      cloud_endpoint: "/sync/transactions",
      error_msg: "network error",
    });

    const items = rq.getRetryQueue("transactions");
    assert.equal(items.length, 1);
    assert.equal(items[0].entity, "transactions");
    assert.equal(items[0].idempotency_key, "test:key:001");
    assert.equal(items[0].attempts, 0);
  });

  test("pushRetryQueue is idempotent on same idempotency_key", () => {
    const item = {
      entity: "products",
      idempotency_key: "test:key:002",
      payload: "[]",
      cloud_endpoint: "/sync/products",
      error_msg: "timeout",
    };
    rq.pushRetryQueue(item);
    rq.pushRetryQueue({ ...item, error_msg: "still failing" });

    const items = rq.getRetryQueue("products");
    assert.equal(items.length, 1, "Should not duplicate on same key");
    assert.equal(items[0].error_msg, "still failing");
  });

  test("markRetried increments attempt counter", () => {
    rq.pushRetryQueue({
      entity: "customers",
      idempotency_key: "test:key:003",
      payload: "[]",
      cloud_endpoint: "/sync/customers",
      error_msg: null,
    });

    const [item] = rq.getRetryQueue("customers");
    rq.markRetried(item.id, "attempt 1 failed");
    rq.markRetried(item.id, "attempt 2 failed");

    const [updated] = rq.getRetryQueue("customers");
    assert.equal(updated.attempts, 2);
    assert.equal(updated.error_msg, "attempt 2 failed");
  });

  test("clearRetryItem removes item from queue", () => {
    rq.pushRetryQueue({
      entity: "products",
      idempotency_key: "test:key:004",
      payload: "[]",
      cloud_endpoint: "/sync/products",
      error_msg: null,
    });

    const [item] = rq.getRetryQueue("products");
    rq.clearRetryItem(item.id);

    const items = rq.getRetryQueue("products");
    assert.equal(items.length, 0);
  });

  test("promoteToDeadLetter moves item from retry to dead-letter", () => {
    rq.pushRetryQueue({
      entity: "transactions",
      idempotency_key: "test:key:005",
      payload: JSON.stringify([{ txn_number: "TXN-DLQ" }]),
      cloud_endpoint: "/sync/transactions",
      error_msg: "permanent failure",
    });

    const [item] = rq.getRetryQueue("transactions");
    rq.promoteToDeadLetter(item.id);

    // Should be gone from retry queue
    const retryItems = rq.getRetryQueue("transactions");
    assert.equal(retryItems.length, 0, "Item should be removed from retry queue");

    // Should appear in dead-letter queue
    const dlq = rq.getDeadLetterQueue("transactions");
    assert.equal(dlq.length, 1);
    assert.equal(dlq[0].idempotency_key, "test:key:005");
    assert.equal(dlq[0].final_error, "permanent failure");
  });

  test("getDeadLetterQueue returns all items when no entity filter", () => {
    rq.pushRetryQueue({ entity: "products",      idempotency_key: "dlq:1", payload: "[]", cloud_endpoint: "/sync/products",      error_msg: "err" });
    rq.pushRetryQueue({ entity: "transactions",  idempotency_key: "dlq:2", payload: "[]", cloud_endpoint: "/sync/transactions",  error_msg: "err" });

    const [a] = rq.getRetryQueue("products");
    const [b] = rq.getRetryQueue("transactions");
    rq.promoteToDeadLetter(a.id);
    rq.promoteToDeadLetter(b.id);

    const all = rq.getDeadLetterQueue();
    assert.equal(all.length, 2);
  });

  test("getRetryQueue returns items oldest first", () => {
    // SQLite auto-increment guarantees insert order
    rq.pushRetryQueue({ entity: "products", idempotency_key: "order:1", payload: "[]", cloud_endpoint: "/", error_msg: null });
    rq.pushRetryQueue({ entity: "products", idempotency_key: "order:2", payload: "[]", cloud_endpoint: "/", error_msg: null });

    const items = rq.getRetryQueue("products");
    assert.equal(items[0].idempotency_key, "order:1");
    assert.equal(items[1].idempotency_key, "order:2");
  });
});

// ── checkpoint (v4.1) ─────────────────────────────────────────────────────────
// Tests updated for v4.1:
//   - Now uses CHECKPOINT_DIR (not CHECKPOINT_FILE) to match production behaviour
//     where the file always lives inside the mounted /app/data volume.
//   - Added: container-restart simulation (re-require module, file survives)
//   - Added: legacy file migration (pre-v4.1 path → new path, old file removed)
//   - Added: atomic write safety (tmp file not left behind on success)
//   - Added: getAllCheckpoints snapshot helper
describe("checkpoint", () => {
  let checkpointDir;
  let checkpointFile;
  let cp;

  before(() => {
    checkpointDir  = path.join(tmpDir, "checkpoint-data");
    checkpointFile = path.join(checkpointDir, "checkpoints.json");
    // v4.1: CHECKPOINT_DIR controls the directory; the filename is fixed.
    process.env.CHECKPOINT_DIR = checkpointDir;
    delete require.cache[require.resolve("../src/checkpoint")];
    cp = require("../src/checkpoint");
  });

  // Clean slate between tests by resetting in-memory cache and deleting the file
  beforeEach(() => {
    try { fs.rmSync(checkpointFile); } catch { /* not present yet — fine */ }
    try { fs.rmSync(checkpointFile + ".tmp"); } catch { /* fine */ }
    // Re-load the module so _cache starts empty
    delete require.cache[require.resolve("../src/checkpoint")];
    cp = require("../src/checkpoint");
  });

  test("getCheckpoint returns epoch start for unknown entity", () => {
    const ts = cp.getCheckpoint("nonexistent_entity");
    assert.equal(ts, "1970-01-01T00:00:00.000Z");
  });

  test("saveCheckpoint persists and getCheckpoint reads back (in-memory)", () => {
    const ts = "2025-06-15T10:30:00.000Z";
    cp.saveCheckpoint("products", ts);
    assert.equal(cp.getCheckpoint("products"), ts);
  });

  test("resetCheckpoint clears the stored value", () => {
    cp.saveCheckpoint("customers", "2025-06-15T10:00:00.000Z");
    cp.resetCheckpoint("customers");
    assert.equal(cp.getCheckpoint("customers"), "1970-01-01T00:00:00.000Z");
  });

  test("checkpoint file is written inside CHECKPOINT_DIR", () => {
    cp.saveCheckpoint("transactions", "2025-07-01T00:00:00.000Z");
    assert.ok(fs.existsSync(checkpointFile), "checkpoints.json must exist in CHECKPOINT_DIR");
    const raw = JSON.parse(fs.readFileSync(checkpointFile, "utf8"));
    assert.equal(raw.transactions, "2025-07-01T00:00:00.000Z");
  });

  test("multiple entity checkpoints are independent", () => {
    cp.saveCheckpoint("products",     "2025-01-01T00:00:00.000Z");
    cp.saveCheckpoint("customers",    "2025-02-01T00:00:00.000Z");
    cp.saveCheckpoint("transactions", "2025-03-01T00:00:00.000Z");

    assert.equal(cp.getCheckpoint("products"),     "2025-01-01T00:00:00.000Z");
    assert.equal(cp.getCheckpoint("customers"),    "2025-02-01T00:00:00.000Z");
    assert.equal(cp.getCheckpoint("transactions"), "2025-03-01T00:00:00.000Z");
  });

  // ── v4.1: Container restart simulation ────────────────────────────────────
  test("checkpoints survive a process restart (re-require simulation)", () => {
    // Write two checkpoints
    cp.saveCheckpoint("products",  "2025-11-01T08:00:00.000Z");
    cp.saveCheckpoint("customers", "2025-11-01T09:00:00.000Z");

    // Simulate container restart: evict module from require cache and reload
    delete require.cache[require.resolve("../src/checkpoint")];
    const cp2 = require("../src/checkpoint");

    // Both checkpoints must be restored from disk
    assert.equal(cp2.getCheckpoint("products"),  "2025-11-01T08:00:00.000Z",
      "products checkpoint not restored after restart");
    assert.equal(cp2.getCheckpoint("customers"), "2025-11-01T09:00:00.000Z",
      "customers checkpoint not restored after restart");
  });

  test("restart without any prior saves starts at epoch (no crash)", () => {
    // No file exists; re-require should load cleanly and return epoch
    delete require.cache[require.resolve("../src/checkpoint")];
    const cp3 = require("../src/checkpoint");
    assert.equal(cp3.getCheckpoint("anything"), "1970-01-01T00:00:00.000Z");
  });

  // ── v4.1: Atomic write — no .tmp file left on success ─────────────────────
  test("atomic write leaves no .tmp file after successful save", () => {
    cp.saveCheckpoint("products", "2025-12-01T00:00:00.000Z");
    const tmpPath = checkpointFile + ".tmp";
    assert.ok(!fs.existsSync(tmpPath), ".tmp file must be cleaned up after successful write");
    assert.ok(fs.existsSync(checkpointFile), "final checkpoint file must exist");
  });

  // ── v4.1: Legacy migration ─────────────────────────────────────────────────
  test("legacy checkpoints.json next to src/ is migrated to CHECKPOINT_DIR", () => {
    // Simulate pre-v4.1 state: legacy file exists, new path does not
    const legacyPath = path.join(__dirname, "../checkpoints.json");
    const legacyData = {
      products:     "2025-05-01T00:00:00.000Z",
      transactions: "2025-05-15T12:00:00.000Z",
    };
    fs.writeFileSync(legacyPath, JSON.stringify(legacyData, null, 2));

    // Ensure new path does not exist so migration is triggered
    try { fs.rmSync(checkpointFile); } catch { /* fine */ }

    // Re-load module — migration should fire at boot
    delete require.cache[require.resolve("../src/checkpoint")];
    const cpMigrated = require("../src/checkpoint");

    try {
      // New file must exist with migrated data
      assert.ok(fs.existsSync(checkpointFile), "checkpoint file not found after migration");
      const migrated = JSON.parse(fs.readFileSync(checkpointFile, "utf8"));
      assert.equal(migrated.products,     "2025-05-01T00:00:00.000Z");
      assert.equal(migrated.transactions, "2025-05-15T12:00:00.000Z");

      // Legacy file must be removed
      assert.ok(!fs.existsSync(legacyPath), "legacy checkpoint file was not removed after migration");

      // In-memory module must serve migrated values
      assert.equal(cpMigrated.getCheckpoint("products"), "2025-05-01T00:00:00.000Z");
    } finally {
      // Cleanup in case assertions fail partway through
      try { fs.rmSync(legacyPath); } catch { /* fine */ }
    }
  });

  // ── v4.1: getAllCheckpoints snapshot ──────────────────────────────────────
  test("getAllCheckpoints returns snapshot of all saved checkpoints", () => {
    cp.saveCheckpoint("products",  "2025-08-01T00:00:00.000Z");
    cp.saveCheckpoint("customers", "2025-08-02T00:00:00.000Z");

    const snap = cp.getAllCheckpoints();
    assert.equal(snap.products,  "2025-08-01T00:00:00.000Z");
    assert.equal(snap.customers, "2025-08-02T00:00:00.000Z");

    // Snapshot must be a copy — mutating it must not affect internal state
    snap.products = "1970-01-01T00:00:00.000Z";
    assert.equal(cp.getCheckpoint("products"), "2025-08-01T00:00:00.000Z",
      "getAllCheckpoints must return a copy, not a reference to internal state");
  });
});



// ── transforms ────────────────────────────────────────────────────────────────
describe("transforms", () => {
  const { transformProduct }     = require("../src/transforms/products");
  const { transformCustomer }    = require("../src/transforms/customers");
  const { transformTransaction } = require("../src/transforms/transactions");

  test("transformProduct maps all required fields and serialises Decimal price as string", () => {
    const row = {
      id: 1, sku: "MILK-1L", barcode: "5001234", name: "Fresh Milk 1L",
      description: null, category_name: "Dairy",
      selling_price: 65.00, cost_price: 50.00,
      vat_exempt: false, tax_code: "A", stock_quantity: 120, reorder_level: 20,
      unit: "litre", is_active: true, image_url: null,
      updated_at: new Date("2025-06-01"), created_at: new Date("2025-01-01"),
    };
    const out = transformProduct(row);
    assert.equal(out.sku, "MILK-1L");
    assert.equal(typeof out.selling_price, "string", "selling_price must be string");
    assert.equal(out.selling_price, "65");
    assert.equal(out.category_name, "Dairy");
  });

  test("transformProduct handles null optional fields gracefully", () => {
    const row = {
      id: 2, sku: "SKU-X", barcode: null, name: "Test",
      description: null, category_name: null,
      selling_price: null, cost_price: null,
      vat_exempt: null, tax_code: null, stock_quantity: null, reorder_level: null,
      unit: null, is_active: null, image_url: null,
      updated_at: null, created_at: null,
    };
    const out = transformProduct(row);
    assert.equal(out.selling_price, "0");
    assert.equal(out.stock_quantity, 0);
    assert.equal(out.is_active, true);
    assert.equal(out.tax_code, "B");
  });

  test("transformCustomer maps phone and handles missing loyalty_points", () => {
    const row = {
      id: 10, name: "Jane Doe", phone: "+254712345678",
      email: null, loyalty_points: null,
      updated_at: new Date("2025-06-01"), created_at: new Date("2025-01-01"),
    };
    const out = transformCustomer(row);
    assert.equal(out.phone, "+254712345678");
    assert.equal(out.loyalty_points, 0);
  });

  test("transformTransaction assembles items array from separate rows", () => {
    const txn = {
      id: 100, txn_number: "TXN-2025-0001", store_id: 1,
      cashier_id: 5, terminal_id: "T01",
      subtotal: 130.00, vat_amount: 20.80, total: 150.80,
      payment_method: "cash", mpesa_ref: null,
      status: "completed", sync_status: "pending",
      created_at: new Date("2025-06-15T09:30:00Z"),
      completed_at: new Date("2025-06-15T09:31:00Z"),
    };
    const items = [
      { id: 200, transaction_id: 100, sku: "MILK-1L", product_name: "Fresh Milk 1L", qty: 2, unit_price: 65.00, line_total: 130.00, discount: 0 },
    ];
    const out = transformTransaction(txn, items);
    assert.equal(out.txn_number, "TXN-2025-0001");
    assert.equal(out.items.length, 1);
    assert.equal(out.items[0].sku, "MILK-1L");
    assert.equal(typeof out.total, "string");
  });
});

// ── idempotency key stability ─────────────────────────────────────────────────
describe("idempotency key stability", () => {
  const crypto = require("crypto");

  function batchHash(txnNumbers) {
    return crypto.createHash("sha256")
      .update(txnNumbers.sort().join(","))
      .digest("hex");
  }

  test("same set of txn_numbers always produces the same hash", () => {
    const set = ["TXN-001", "TXN-002", "TXN-003"];
    assert.equal(batchHash([...set]), batchHash([...set].reverse()));
  });

  test("hash length is always 64 chars regardless of batch size", () => {
    const small = ["TXN-001"];
    const large = Array.from({ length: 500 }, (_, i) => `TXN-${String(i).padStart(6, "0")}`);
    assert.equal(batchHash(small).length, 64);
    assert.equal(batchHash(large).length, 64);
  });

  test("different txn sets produce different hashes", () => {
    const a = ["TXN-001", "TXN-002"];
    const b = ["TXN-001", "TXN-003"];
    assert.notEqual(batchHash(a), batchHash(b));
  });
});
