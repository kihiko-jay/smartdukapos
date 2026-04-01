#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DukaPOS — Database Restore Script
#
# Restores from a .sql.gz backup file created by backup.sh.
# Before restoring, takes a pre-restore snapshot of the current DB.
#
# Usage:
#   ./restore.sh /backups/dukapos_20250315_020001.sql.gz
#   ./restore.sh /backups/dukapos_20250315_020001.sql.gz --confirm
#
# The --confirm flag skips the interactive prompt (for automated recovery).
#
# Required env vars:
#   DATABASE_URL   postgresql://user:pass@host:5432/dbname
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BACKUP_FILE="${1:-}"
CONFIRM_FLAG="${2:-}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_PREFIX="[dukapos-restore][${TIMESTAMP}]"

# ── Validate input ────────────────────────────────────────────────────────────
if [[ -z "${BACKUP_FILE}" ]]; then
    echo "Usage: $0 <backup_file.sql.gz> [--confirm]" >&2
    exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "${LOG_PREFIX} ERROR: Backup file not found: ${BACKUP_FILE}" >&2
    exit 1
fi

if ! gzip -t "${BACKUP_FILE}" 2>/dev/null; then
    echo "${LOG_PREFIX} ERROR: Backup file failed integrity check (corrupt gzip)" >&2
    exit 1
fi

# ── Parse DATABASE_URL ────────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "${LOG_PREFIX} ERROR: DATABASE_URL is not set" >&2
    exit 1
fi

DB_URL="${DATABASE_URL#postgresql://}"
DB_URL="${DB_URL#postgres://}"
DB_USER="${DB_URL%%:*}"
DB_URL="${DB_URL#*:}"
DB_PASS="${DB_URL%%@*}"
DB_URL="${DB_URL#*@}"
DB_HOST="${DB_URL%%:*}"
DB_URL="${DB_URL#*:}"
DB_PORT="${DB_URL%%/*}"
DB_NAME="${DB_URL#*/}"

export PGPASSWORD="${DB_PASS}"

# ── Confirmation gate ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║          ⚠️  DukaPOS DATABASE RESTORE — DESTRUCTIVE OP           ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Target DB  : ${DB_NAME}@${DB_HOST}:${DB_PORT}"
echo "║  Backup     : $(basename "${BACKUP_FILE}")"
echo "║  Backup size: $(du -sh "${BACKUP_FILE}" | cut -f1)"
echo "║"
echo "║  ⚠️  THIS WILL DROP AND RECREATE ALL TABLES IN ${DB_NAME}"
echo "║  A pre-restore snapshot will be saved automatically."
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

if [[ "${CONFIRM_FLAG}" != "--confirm" ]]; then
    read -r -p "Type 'RESTORE' to proceed: " user_input
    if [[ "${user_input}" != "RESTORE" ]]; then
        echo "${LOG_PREFIX} Restore cancelled."
        exit 0
    fi
fi

# ── Pre-restore snapshot ──────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/backups}"
PRE_RESTORE_SNAPSHOT="${BACKUP_DIR}/dukapos_pre_restore_${TIMESTAMP}.sql.gz"

echo "${LOG_PREFIX} Taking pre-restore snapshot → ${PRE_RESTORE_SNAPSHOT}"
mkdir -p "${BACKUP_DIR}"

pg_dump \
    --host="${DB_HOST}" \
    --port="${DB_PORT}" \
    --username="${DB_USER}" \
    --dbname="${DB_NAME}" \
    --format=plain \
    --no-owner \
    --no-privileges \
    2>/dev/null | gzip -9 > "${PRE_RESTORE_SNAPSHOT}"

if [[ ! -s "${PRE_RESTORE_SNAPSHOT}" ]]; then
    echo "${LOG_PREFIX} WARNING: Pre-restore snapshot is empty (DB may be empty or inaccessible)"
else
    echo "${LOG_PREFIX} Pre-restore snapshot saved ($(du -sh "${PRE_RESTORE_SNAPSHOT}" | cut -f1))"
fi

# ── Terminate active connections ──────────────────────────────────────────────
echo "${LOG_PREFIX} Terminating active connections to ${DB_NAME}..."

psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres <<-SQL
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '${DB_NAME}'
      AND pid <> pg_backend_pid();
SQL

# ── Drop and recreate target DB ───────────────────────────────────────────────
echo "${LOG_PREFIX} Dropping and recreating database ${DB_NAME}..."

psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${DB_NAME};"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
    -c "CREATE DATABASE ${DB_NAME} WITH OWNER ${DB_USER} ENCODING 'UTF8' LC_COLLATE='en_US.UTF-8' LC_CTYPE='en_US.UTF-8' TEMPLATE template0;"

echo "${LOG_PREFIX} Database recreated. Restoring from backup..."

# ── Restore ───────────────────────────────────────────────────────────────────
gunzip -c "${BACKUP_FILE}" | \
    psql \
        --host="${DB_HOST}" \
        --port="${DB_PORT}" \
        --username="${DB_USER}" \
        --dbname="${DB_NAME}" \
        --echo-errors \
        2>&1 | tee "${BACKUP_DIR}/restore_${TIMESTAMP}.log"

echo "${LOG_PREFIX} Restore complete. Running post-restore checks..."

# ── Post-restore validation ───────────────────────────────────────────────────
TXN_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    -t -c "SELECT COUNT(*) FROM transactions;" 2>/dev/null | xargs)
PROD_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    -t -c "SELECT COUNT(*) FROM products;" 2>/dev/null | xargs)
EMP_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    -t -c "SELECT COUNT(*) FROM employees;" 2>/dev/null | xargs)

echo ""
echo "${LOG_PREFIX} ✅ Restore complete"
echo "${LOG_PREFIX}   Transactions : ${TXN_COUNT}"
echo "${LOG_PREFIX}   Products     : ${PROD_COUNT}"
echo "${LOG_PREFIX}   Employees    : ${EMP_COUNT}"
echo ""
echo "${LOG_PREFIX} ⚠️  IMPORTANT: Run Alembic migrations if restoring to a newer schema version:"
echo "               alembic upgrade head"
echo ""
echo "${LOG_PREFIX} Pre-restore snapshot kept at: ${PRE_RESTORE_SNAPSHOT}"
echo "${LOG_PREFIX} Restore log: ${BACKUP_DIR}/restore_${TIMESTAMP}.log"

unset PGPASSWORD
exit 0
