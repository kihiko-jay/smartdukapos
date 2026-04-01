/**
 * Persistent retry queue for the sync agent.
 *
 * Backed by SQLite (better-sqlite3) — survives process crashes and restarts.
 * Each failed cloud batch is stored here with its idempotency key and payload.
 * On the next sync cycle, drainRetryQueue() replays these before processing new records.
 *
 * This ensures NO transaction is ever silently dropped due to a transient
 * network error, cloud downtime, or process crash.
 */

const Database = require("better-sqlite3");
const path     = require("path");
const logger   = require("./logger");

const DB_PATH = process.env.RETRY_QUEUE_PATH
  || path.join(process.cwd(), "data", "retry_queue.db");

let db;

function getDb() {
  if (db) return db;

  // Ensure data directory exists
  const fs  = require("fs");
  const dir = path.dirname(DB_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  db = new Database(DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("synchronous = NORMAL");

  db.exec(`
    CREATE TABLE IF NOT EXISTS retry_queue (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      entity           TEXT    NOT NULL,
      idempotency_key  TEXT    NOT NULL UNIQUE,
      payload          TEXT    NOT NULL,
      cloud_endpoint   TEXT    NOT NULL,
      error_msg        TEXT,
      attempts         INTEGER NOT NULL DEFAULT 0,
      last_attempted   INTEGER,
      created_at       INTEGER NOT NULL DEFAULT (unixepoch('now') * 1000)
    );
    CREATE INDEX IF NOT EXISTS idx_rq_entity ON retry_queue(entity);

    -- Dead-letter queue: items that exceeded MAX_ATTEMPTS are moved here
    -- for manual inspection. Nothing is silently discarded.
    CREATE TABLE IF NOT EXISTS dead_letter_queue (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      entity           TEXT    NOT NULL,
      idempotency_key  TEXT    NOT NULL UNIQUE,
      payload          TEXT    NOT NULL,
      cloud_endpoint   TEXT    NOT NULL,
      final_error      TEXT,
      attempts         INTEGER NOT NULL,
      created_at       INTEGER NOT NULL,
      promoted_at      INTEGER NOT NULL DEFAULT (unixepoch('now') * 1000)
    );
    CREATE INDEX IF NOT EXISTS idx_dlq_entity ON dead_letter_queue(entity);
  `);

  logger.info("Retry queue SQLite initialised", { path: DB_PATH });
  return db;
}

/**
 * Push a failed batch to the retry queue.
 * Uses INSERT OR REPLACE so the same idempotency_key is updated (not duplicated) on re-failure.
 */
function pushRetryQueue({ entity, idempotency_key, payload, cloud_endpoint, error_msg }) {
  try {
    getDb().prepare(`
      INSERT INTO retry_queue (entity, idempotency_key, payload, cloud_endpoint, error_msg)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(idempotency_key) DO UPDATE SET
        error_msg      = excluded.error_msg,
        attempts       = attempts,
        last_attempted = NULL
    `).run(entity, idempotency_key, payload, cloud_endpoint, error_msg || null);
    logger.warn("Pushed to retry queue", { entity, idempotency_key });
  } catch (err) {
    logger.error("Failed to write to retry queue", { error: err.message });
  }
}

/**
 * Return all pending retry items for a given entity, oldest first.
 */
function getRetryQueue(entity) {
  try {
    return getDb().prepare(`
      SELECT * FROM retry_queue
      WHERE entity = ?
      ORDER BY created_at ASC
    `).all(entity);
  } catch (err) {
    logger.error("Failed to read retry queue", { error: err.message });
    return [];
  }
}

/**
 * Record a failed retry attempt (increments counter, logs error).
 */
function markRetried(id, errorMsg) {
  try {
    getDb().prepare(`
      UPDATE retry_queue
      SET attempts = attempts + 1, error_msg = ?, last_attempted = unixepoch('now') * 1000
      WHERE id = ?
    `).run(errorMsg || null, id);
  } catch (err) {
    logger.error("Failed to update retry queue", { error: err.message });
  }
}

/**
 * Remove a successfully replayed item.
 */
function clearRetryItem(id) {
  try {
    getDb().prepare("DELETE FROM retry_queue WHERE id = ?").run(id);
  } catch (err) {
    logger.error("Failed to clear retry queue item", { error: err.message });
  }
}

/**
 * Promote a permanently-failed item to the dead-letter queue.
 * The original retry_queue row is deleted after promotion.
 * Dead-letter items are kept indefinitely for manual inspection / replay.
 */
function promoteToDeadLetter(id) {
  try {
    const db = getDb();
    const item = db.prepare("SELECT * FROM retry_queue WHERE id = ?").get(id);
    if (!item) return;

    db.prepare(`
      INSERT OR REPLACE INTO dead_letter_queue
        (entity, idempotency_key, payload, cloud_endpoint, final_error, attempts, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(
      item.entity, item.idempotency_key, item.payload,
      item.cloud_endpoint, item.error_msg, item.attempts, item.created_at
    );
    db.prepare("DELETE FROM retry_queue WHERE id = ?").run(id);

    logger.error("Item promoted to dead-letter queue", {
      entity:          item.entity,
      idempotency_key: item.idempotency_key,
      attempts:        item.attempts,
    });
  } catch (err) {
    logger.error("Failed to promote to dead-letter queue", { error: err.message });
  }
}

/**
 * Return all dead-letter items (for monitoring / alerting).
 */
function getDeadLetterQueue(entity) {
  try {
    const query = entity
      ? "SELECT * FROM dead_letter_queue WHERE entity = ? ORDER BY promoted_at DESC"
      : "SELECT * FROM dead_letter_queue ORDER BY promoted_at DESC";
    return entity
      ? getDb().prepare(query).all(entity)
      : getDb().prepare(query).all();
  } catch (err) {
    logger.error("Failed to read dead-letter queue", { error: err.message });
    return [];
  }
}

module.exports = { pushRetryQueue, getRetryQueue, markRetried, clearRetryItem, promoteToDeadLetter, getDeadLetterQueue };
