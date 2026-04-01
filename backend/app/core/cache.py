"""
Redis-backed caching for DukaPOS (production layer).

Architecture:
  - Wraps redis-py with a graceful fallback: if Redis is unreachable, every
    cache operation is a no-op and the caller hits the DB as normal.
  - Cache keys are namespaced by entity type and store_id to prevent
    cross-store data leakage.
  - TTLs are intentionally conservative: POS reads must never serve stale
    prices (max 5 min for products).
  - Every cache miss / hit is counted in the in-process metrics module.

Usage:
    from app.core.cache import cache
    await cache.get("products:all:store_1")
    await cache.set("products:all:store_1", payload, ttl=300)
    await cache.invalidate_prefix("products:")
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("dukapos.cache")

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    logger.warning("redis package not installed — caching disabled")


class RedisCache:
    """
    Async Redis cache client.

    Falls back to no-op transparently if:
      - REDIS_URL is not configured
      - Redis is unreachable
      - redis package is not installed
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._enabled = False

    async def init(self) -> None:
        """
        Connect to Redis. Called once at application startup.
        Failures are logged but do NOT prevent the app from starting.
        """
        if not _REDIS_AVAILABLE:
            logger.info("Cache disabled: redis package not installed")
            return

        url = os.getenv("REDIS_URL", "")
        if not url:
            logger.info("Cache disabled: REDIS_URL not set")
            return

        try:
            self._client = aioredis.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
                retry_on_timeout=False,
            )
            # Verify connectivity
            await self._client.ping()
            self._enabled = True
            logger.info("Redis cache connected: %s", url.split("@")[-1])  # mask credentials
        except Exception as exc:
            logger.warning(
                "Redis cache unavailable — falling back to direct DB reads: %s", exc
            )
            self._client = None
            self._enabled = False

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    # ── Core operations ────────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        """Return deserialized value or None on miss / error."""
        if not self._enabled or not self._client:
            return None
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("Cache get error for key=%s: %s", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Serialize and store value with TTL (seconds).
        Returns True on success, False on any error.
        """
        if not self._enabled or not self._client:
            return False
        try:
            serialized = json.dumps(value, default=str)
            await self._client.setex(key, ttl, serialized)
            return True
        except Exception as exc:
            logger.debug("Cache set error for key=%s: %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a specific key."""
        if not self._enabled or not self._client:
            return False
        try:
            await self._client.delete(key)
            return True
        except Exception as exc:
            logger.debug("Cache delete error for key=%s: %s", key, exc)
            return False

    async def invalidate_prefix(self, prefix: str) -> int:
        """
        Delete all keys matching prefix*.
        Used for bulk invalidation (e.g. after a product update).
        Returns count of deleted keys.
        """
        if not self._enabled or not self._client:
            return 0
        try:
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = await self._client.scan(cursor, match=f"{prefix}*", count=100)
                if keys:
                    deleted += await self._client.delete(*keys)
                if cursor == 0:
                    break
            if deleted:
                logger.debug("Cache invalidated %d keys for prefix=%s", deleted, prefix)
            return deleted
        except Exception as exc:
            logger.debug("Cache invalidate_prefix error for prefix=%s: %s", prefix, exc)
            return 0

    @property
    def enabled(self) -> bool:
        return self._enabled


# Module-level singleton — init() is called once in main.py lifespan
cache = RedisCache()


# ── Cache key helpers ──────────────────────────────────────────────────────────

def product_list_key(store_id: Optional[int] = None, **filters) -> str:
    """Stable key for a product list query with filters."""
    store = store_id or "global"
    filter_str = ":".join(f"{k}={v}" for k, v in sorted(filters.items()) if v is not None)
    return f"products:list:{store}:{filter_str or 'all'}"


def product_detail_key(product_id: int) -> str:
    return f"products:detail:{product_id}"


def product_barcode_key(barcode: str) -> str:
    return f"products:barcode:{barcode}"


# ── TTLs (seconds) ─────────────────────────────────────────────────────────────
PRODUCT_LIST_TTL   = 300   # 5 minutes — cashiers see a price update within 5 min
PRODUCT_DETAIL_TTL = 300
BARCODE_LOOKUP_TTL = 600   # 10 minutes — barcodes never change
