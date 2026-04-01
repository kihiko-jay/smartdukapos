/**
 * DukaPOS Sync Agent — Entry Point
 *
 * Runs as a background service (PM2 / Windows Service).
 * Schedules periodic sync cycles for each entity type.
 *
 * Architecture:
 *   - Products, Customers  → every 60s  (timestamp CDC)
 *   - Transactions         → every 10s  (outbox pattern, near-real-time)
 *   - Cloud → Local pulls  → every 5min (price/catalog updates)
 *
 * Safety guarantees:
 *   - Overlapping runs are prevented (lock flag per entity)
 *   - All errors are caught and logged — agent never crashes
 *   - Checkpoints only advance after confirmed cloud write
 */

require("dotenv").config();
const cron   = require("node-cron");
const logger = require("./logger");
const {
  syncProducts,
  syncCustomers,
  syncTransactions,
  pullCloudUpdates,
} = require("./syncLoop");

const { localPool } = require("./db");

// ── Per-entity running locks ──────────────────────────────────────────────────
const running = {
  products:     false,
  customers:    false,
  transactions: false,
  cloudPull:    false,
};

function guard(name, fn) {
  return async () => {
    if (running[name]) {
      logger.debug(`Skipping ${name} sync — previous run still active`);
      return;
    }
    running[name] = true;
    try {
      await fn();
    } catch (err) {
      logger.error(`Unhandled error in ${name} sync`, { error: err.message, stack: err.stack });
    } finally {
      running[name] = false;
    }
  };
}

// ── Scheduler ─────────────────────────────────────────────────────────────────
function startScheduler() {
  const txnInterval  = parseInt(process.env.SYNC_INTERVAL_TRANSACTIONS || "10");
  const prodInterval = parseInt(process.env.SYNC_INTERVAL_PRODUCTS     || "60");
  const custInterval = parseInt(process.env.SYNC_INTERVAL_CUSTOMERS    || "60");

  logger.info("DukaPOS Sync Agent starting", {
    store_id:      process.env.STORE_ID,
    cloud_api:     process.env.CLOUD_API_URL,
    txn_interval:  `${txnInterval}s`,
    prod_interval: `${prodInterval}s`,
  });

  // Transactions — near real-time
  cron.schedule(`*/${txnInterval} * * * * *`, guard("transactions", syncTransactions));

  // Products
  cron.schedule(`*/${prodInterval} * * * * *`, guard("products", syncProducts));

  // Customers
  cron.schedule(`*/${custInterval} * * * * *`, guard("customers", syncCustomers));

  // Cloud → Local pulls every 5 minutes
  cron.schedule("*/5 * * * *", guard("cloudPull", pullCloudUpdates));

  logger.info("✅ Sync agent running. Press Ctrl+C to stop.");
}

// ── Health check ──────────────────────────────────────────────────────────────
async function healthCheck() {
  try {
    await localPool.query("SELECT 1");
    logger.info("✅ Local DB connection healthy");
  } catch (err) {
    logger.error("❌ Local DB connection failed", { error: err.message });
    process.exit(1);
  }
}

// ── Graceful shutdown ─────────────────────────────────────────────────────────
process.on("SIGINT",  () => { logger.info("Sync agent stopping (SIGINT)");  localPool.end(); process.exit(0); });
process.on("SIGTERM", () => { logger.info("Sync agent stopping (SIGTERM)"); localPool.end(); process.exit(0); });
process.on("uncaughtException",  (err) => logger.error("Uncaught exception",  { error: err.message, stack: err.stack }));
process.on("unhandledRejection", (err) => logger.error("Unhandled rejection", { error: err?.message }));

// ── Start ─────────────────────────────────────────────────────────────────────
healthCheck().then(startScheduler);
