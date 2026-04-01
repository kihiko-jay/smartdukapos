#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DukaPOS — Automated PostgreSQL Backup Script
#
# Produces a timestamped, gzip-compressed pg_dump.
# Optionally uploads to S3 (if AWS_S3_BACKUP_BUCKET is set).
# Prunes local files older than RETENTION_DAYS (default: 30).
# Sends a Slack alert on failure (if SLACK_WEBHOOK_URL is set).
#
# Cron example (daily at 2AM server time):
#   0 2 * * * /app/scripts/backup.sh >> /var/log/dukapos-backup.log 2>&1
#
# Required env vars:
#   DATABASE_URL   postgresql://user:pass@host:5432/dbname
#
# Optional env vars:
#   BACKUP_DIR             local directory for backup files (default: /backups)
#   AWS_S3_BACKUP_BUCKET   e.g. s3://my-company-dukapos-backups
#   RETENTION_DAYS         days to keep local backups (default: 30)
#   SLACK_WEBHOOK_URL      for failure alerts
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/dukapos_${TIMESTAMP}.sql.gz"
LOG_PREFIX="[dukapos-backup][${TIMESTAMP}]"

# ── Parse DATABASE_URL ────────────────────────────────────────────────────────
# Expected format: postgresql://user:password@host:port/dbname
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "${LOG_PREFIX} ERROR: DATABASE_URL is not set" >&2
    exit 1
fi

# Strip scheme prefix (postgres:// or postgresql://)
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

# ── Ensure backup directory exists ────────────────────────────────────────────
mkdir -p "${BACKUP_DIR}"

echo "${LOG_PREFIX} Starting backup of ${DB_NAME} on ${DB_HOST}:${DB_PORT}"

# ── Pre-backup: capture DB size (informational) ───────────────────────────────
DB_SIZE=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    -t -c "SELECT pg_size_pretty(pg_database_size('${DB_NAME}'));" 2>/dev/null | xargs || echo "unknown")
echo "${LOG_PREFIX} DB size: ${DB_SIZE}"

# ── pg_dump ───────────────────────────────────────────────────────────────────
echo "${LOG_PREFIX} Running pg_dump → ${BACKUP_FILE}"

pg_dump \
    --host="${DB_HOST}" \
    --port="${DB_PORT}" \
    --username="${DB_USER}" \
    --dbname="${DB_NAME}" \
    --format=plain \
    --no-owner \
    --no-privileges \
    --verbose \
    2>&1 | gzip -9 > "${BACKUP_FILE}"

BACKUP_SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
echo "${LOG_PREFIX} Backup complete: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Verify the backup is non-empty and can be read
if [[ ! -s "${BACKUP_FILE}" ]]; then
    echo "${LOG_PREFIX} ERROR: Backup file is empty!" >&2
    _notify_slack "DukaPOS backup FAILED: empty backup file at ${TIMESTAMP}" || true
    exit 1
fi

# Quick integrity check: gzip -t will fail if the file is corrupt
if ! gzip -t "${BACKUP_FILE}" 2>/dev/null; then
    echo "${LOG_PREFIX} ERROR: Backup file is corrupt (gzip test failed)!" >&2
    _notify_slack "DukaPOS backup FAILED: corrupt gzip at ${TIMESTAMP}" || true
    exit 1
fi

echo "${LOG_PREFIX} Integrity check passed"

# ── Upload to S3 (optional) ───────────────────────────────────────────────────
if [[ -n "${AWS_S3_BACKUP_BUCKET:-}" ]]; then
    S3_KEY="${AWS_S3_BACKUP_BUCKET}/$(basename "${BACKUP_FILE}")"
    echo "${LOG_PREFIX} Uploading to ${S3_KEY}"

    if aws s3 cp "${BACKUP_FILE}" "${S3_KEY}" \
        --sse AES256 \
        --storage-class STANDARD_IA \
        --metadata "db=${DB_NAME},timestamp=${TIMESTAMP},size=${DB_SIZE}"; then
        echo "${LOG_PREFIX} S3 upload successful: ${S3_KEY}"
    else
        echo "${LOG_PREFIX} WARNING: S3 upload failed — backup retained locally" >&2
        _notify_slack "DukaPOS S3 upload failed for backup at ${TIMESTAMP}" || true
        # Do NOT exit — local backup still exists
    fi
fi

# ── Prune old local backups ───────────────────────────────────────────────────
echo "${LOG_PREFIX} Pruning backups older than ${RETENTION_DAYS} days"
PRUNED=$(find "${BACKUP_DIR}" -name "dukapos_*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l | xargs)
echo "${LOG_PREFIX} Pruned ${PRUNED} old backup(s)"

# ── Summary ───────────────────────────────────────────────────────────────────
REMAINING=$(find "${BACKUP_DIR}" -name "dukapos_*.sql.gz" | wc -l | xargs)
echo "${LOG_PREFIX} SUCCESS — DB: ${DB_NAME}, Size: ${BACKUP_SIZE}, Retained locally: ${REMAINING} backup(s)"

# ── Slack alert (failure only) ────────────────────────────────────────────────
_notify_slack() {
    local message="$1"
    if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
        curl -s -X POST "${SLACK_WEBHOOK_URL}" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"🔴 ${message}\"}" > /dev/null 2>&1 || true
    fi
}

unset PGPASSWORD
exit 0
