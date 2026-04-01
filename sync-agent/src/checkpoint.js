/**
 * Checkpoint manager — v4.1
 *
 * Persists the last successfully synced timestamp per entity so that a
 * container restart resumes from where it left off instead of replaying
 * the entire history.
 *
 * STORAGE PATH (v4.1 fix):
 *   /app/data/checkpoints.json   ← inside the Docker-mounted data volume
 *
 *   The production compose mounts /app/data as a named volume, so this
 *   file survives container restarts and image upgrades.
 *
 *   If the old unversioned path (next to the source file) exists it is
 *   migrated automatically on first boot and then deleted.
 *
 * CRASH SAFETY:
 *   Writes are atomic — written to a .tmp sibling first, then renamed.
 *   A crash mid-write leaves the previous checkpoint intact.
 */

"use strict";

const fs   = require("fs");
const path = require("path");

// ── Paths ─────────────────────────────────────────────────────────────────────

const DATA_DIR        = process.env.CHECKPOINT_DIR || "/app/data";
const CHECKPOINT_FILE = path.join(DATA_DIR, "checkpoints.json");

// Legacy path (pre-v4.1) used for one-time migration only.
const LEGACY_FILE = path.join(__dirname, "../checkpoints.json");

// ── Internal helpers ──────────────────────────────────────────────────────────

function _ensureDir() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function _atomicWrite(filePath, data) {
  const tmp = filePath + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
}

function _migrateLegacy() {
  if (fs.existsSync(LEGACY_FILE) && !fs.existsSync(CHECKPOINT_FILE)) {
    try {
      _ensureDir();
      const legacyData = JSON.parse(fs.readFileSync(LEGACY_FILE, "utf8"));
      _atomicWrite(CHECKPOINT_FILE, legacyData);
      fs.unlinkSync(LEGACY_FILE);
      console.log("[checkpoint] Migrated legacy checkpoint file to", CHECKPOINT_FILE);
    } catch (err) {
      console.warn("[checkpoint] Legacy migration failed (non-fatal):", err.message);
    }
  }
}

function _loadFromDisk() {
  try {
    if (fs.existsSync(CHECKPOINT_FILE)) {
      return JSON.parse(fs.readFileSync(CHECKPOINT_FILE, "utf8"));
    }
  } catch (err) {
    console.warn("[checkpoint] Failed to load checkpoints — starting fresh:", err.message);
  }
  return {};
}

// ── Boot sequence ─────────────────────────────────────────────────────────────

_ensureDir();
_migrateLegacy();
let _cache = _loadFromDisk();

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Get the last_synced_at timestamp for an entity.
 * Returns epoch start if never synced.
 */
function getCheckpoint(entity) {
  return _cache[entity] || "1970-01-01T00:00:00.000Z";
}

/**
 * Advance the checkpoint to the latest timestamp seen.
 * Only call after confirmed successful cloud write.
 */
function saveCheckpoint(entity, timestamp) {
  _cache[entity] = timestamp;
  try {
    _ensureDir();
    _atomicWrite(CHECKPOINT_FILE, _cache);
  } catch (err) {
    console.error("[checkpoint] Write failed (will retry next save):", err.message);
  }
}

/**
 * Reset a checkpoint — causes a full re-sync for that entity on next run.
 * Intended for manual operator intervention only.
 */
function resetCheckpoint(entity) {
  delete _cache[entity];
  try {
    _ensureDir();
    _atomicWrite(CHECKPOINT_FILE, _cache);
  } catch (err) {
    console.error("[checkpoint] Write failed during reset:", err.message);
  }
}

/**
 * Return a snapshot of all checkpoints (for health checks / diagnostics).
 */
function getAllCheckpoints() {
  return { ..._cache };
}

module.exports = { getCheckpoint, saveCheckpoint, resetCheckpoint, getAllCheckpoints };
