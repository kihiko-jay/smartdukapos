/**
 * Database connections for the DukaPOS sync agent.
 *
 * Two pools are exported:
 *   localPool  — read-only, used for CDC queries (products, customers, transactions)
 *   writePool  — read-write, used ONLY by pullCloudUpdates() to apply price changes
 *
 * PATCH I-01: writePool replaces the per-cycle `new Pool()` that was created and
 * destroyed inside pullCloudUpdates(). A persistent pool eliminates connection
 * exhaustion and avoids repeated TCP handshake overhead on every sync cycle.
 */
const { Pool } = require("pg");
require("dotenv").config();

const _dbConfig = {
  host:                   process.env.LOCAL_DB_HOST || "localhost",
  port:                   parseInt(process.env.LOCAL_DB_PORT || "5432"),
  database:               process.env.LOCAL_DB_NAME || "dukapos_db",
  user:                   process.env.LOCAL_DB_USER || "dukapos",
  password:               process.env.LOCAL_DB_PASS || "dukapos_pass",
  idleTimeoutMillis:      30000,
  connectionTimeoutMillis: 5000,
};

// ── Read-only pool (CDC reads — products, customers, transactions) ──────────
const localPool = new Pool({
  ..._dbConfig,
  max:     5,
  options: "-c default_transaction_read_only=on",  // safety: can never write
});

localPool.on("error", (err) => {
  const logger = require("./logger");
  logger.error("Local DB pool error", { error: err.message });
});

// ── Write pool (cloud→local product price updates only) ────────────────────
// Kept small (max 2) since writes are infrequent (every cloud pull cycle).
// NEVER use this pool for CDC reads — it has no read_only guard.
const writePool = new Pool({
  ..._dbConfig,
  max: 2,
});

writePool.on("error", (err) => {
  const logger = require("./logger");
  logger.error("Write DB pool error", { error: err.message });
});

module.exports = { localPool, writePool };
