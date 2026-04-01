# ─────────────────────────────────────────────────────────────────────────────
# DukaPOS Sync Agent — Dockerfile
#
# Multi-stage build:
#   deps    — install production dependencies only (no devDependencies)
#   runtime — minimal Alpine image with only what's needed to run
#
# Usage:
#   docker build -t dukapos-sync-agent .
#   docker run --env-file .env -v sync-data:/app/data dukapos-sync-agent
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: install deps ─────────────────────────────────────────────────────
FROM node:20-alpine AS deps

WORKDIR /app

# Copy manifests first for layer caching
COPY package.json ./

# Install production dependencies only
# better-sqlite3 requires build tools (node-pre-gyp / native bindings)
RUN apk add --no-cache python3 make g++ && \
    npm install --omit=dev && \
    apk del python3 make g++


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM node:20-alpine AS runtime

LABEL org.opencontainers.image.title="DukaPOS Sync Agent"
LABEL org.opencontainers.image.description="Local → cloud data synchronisation agent"
LABEL org.opencontainers.image.version="2.0.0"

WORKDIR /app

# Non-root user for least-privilege execution
RUN addgroup -S syncagent && adduser -S syncagent -G syncagent

# Copy installed modules from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy source
COPY src/ ./src/
COPY package.json ./

# Persistent volumes:
#   /app/data  — SQLite retry queue + dead-letter db (retry_queue.db)
#   /app/logs  — Agent log files
#   /app       — checkpoints.json is written to the WORKDIR
VOLUME ["/app/data", "/app/logs"]

# Environment defaults (override via .env or docker run --env)
ENV NODE_ENV=production \
    LOG_LEVEL=info \
    RETRY_QUEUE_PATH=/app/data/retry_queue.db \
    SYNC_INTERVAL_TRANSACTIONS=10 \
    SYNC_INTERVAL_PRODUCTS=60 \
    SYNC_INTERVAL_CUSTOMERS=60 \
    BATCH_SIZE=500

# Ensure data / logs dirs exist and are owned by syncagent
RUN mkdir -p /app/data /app/logs && \
    chown -R syncagent:syncagent /app

USER syncagent

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD node -e "require('fs').statSync('${RETRY_QUEUE_PATH:-/app/data/retry_queue.db}'); process.exit(0);" \
       2>/dev/null || exit 1

CMD ["node", "src/index.js"]
