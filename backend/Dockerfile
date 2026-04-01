# ─────────────────────────────────────────────────────────────────────────────
# DukaPOS Backend — Production Dockerfile (PATCH E-02)
#
# Multi-stage build:
#   Stage 1 (builder) — installs Python dependencies into an isolated prefix
#   Stage 2 (runtime) — copies only what is needed, runs as non-root user
#
# Key production hardening vs the dev docker-compose config:
#   - No --reload (source hot-reload disabled)
#   - No volume mount (source code is baked into image)
#   - Non-root user (dukapos:dukapos)
#   - 4 uvicorn workers (tune with WORKERS env var)
#   - Health check wired to /health
#   - PYTHONDONTWRITEBYTECODE + PYTHONUNBUFFERED for clean logging
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Build-time deps (gcc for psycopg2-binary compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into /install prefix so we can copy cleanly to runtime stage
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime deps only (libpq5 for psycopg2, curl for HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — the app never needs to write to the filesystem
RUN groupadd -r dukapos && useradd -r -g dukapos -d /app -s /bin/false dukapos

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Remove development artefacts that should not be in the production image
RUN rm -rf \
    tests/ \
    .env \
    .env.example \
    __pycache__ \
    alembic/versions/__pycache__ \
    && find . -name "*.pyc" -delete \
    && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Hand ownership to the runtime user
RUN chown -R dukapos:dukapos /app

USER dukapos

# Python environment hardening
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Shallow /health probe — load balancers poll this every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production command:
#   - 4 workers (adjust WORKERS for your instance size: 2*CPU+1 is a good start)
#   - No --reload
#   - Structured JSON logs via log_config (set LOG_LEVEL in your .env)
#
# Alembic migrations should be run BEFORE starting the API:
#   docker run --rm --env-file .env dukapos-api alembic upgrade head
CMD ["sh", "-c", \
     "uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers ${WORKERS:-4} \
      --log-level ${LOG_LEVEL:-info} \
      --no-access-log"]
