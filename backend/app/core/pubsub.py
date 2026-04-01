"""
Redis pub/sub bridge for WebSocket scaling (Step 6.3)

Problem:
  ConnectionManager lives in-process. With 4 uvicorn workers, a worker that
  handles an M-Pesa callback may not be the same worker that owns the
  terminal's WebSocket connection — so notify_mpesa_confirmed() silently drops
  the event 75% of the time.

Solution:
  Introduce a Redis pub/sub channel ("dukapos:ws:events"). Any worker can
  PUBLISH an event. A background subscriber task in every worker SUBSCRIBES and
  dispatches to locally-connected terminals.

Architecture:
  [Worker A — receives M-Pesa callback]
       ↓  PUBLISH "dukapos:ws:events" → {"terminal_id": "T01", "event": {...}}
  [Redis]
       ↓  fan-out to all subscribers
  [Worker B — owns WebSocket for T01]
       ↓  receives message → manager.send("T01", event)

Backward compatibility:
  - If Redis is unavailable, falls back to direct in-process send (original behaviour)
  - No change to the existing notify_* API surface
  - The notifier.py module-level `manager` singleton is unchanged; pub/sub is
    an additive layer that intercepts publish calls.

Usage (in main.py lifespan):
    from app.core.pubsub import ws_pubsub
    await ws_pubsub.start(redis_url=settings.REDIS_URL)
    ...
    await ws_pubsub.stop()

Usage (in notify helpers — replaces manager.send):
    await ws_pubsub.publish(terminal_id, message)
"""

import asyncio
import json
import logging
from typing import Optional, Any

logger = logging.getLogger("dukapos.pubsub")

_CHANNEL = "dukapos:ws:events"


class WebSocketPubSub:
    """
    Redis pub/sub bridge for cross-worker WebSocket delivery.
    Falls back to direct in-process send when Redis is unavailable.
    """

    def __init__(self) -> None:
        self._redis = None
        self._pubsub = None
        self._subscriber_task: Optional[asyncio.Task] = None
        self._enabled = False

    async def start(self, redis_url: str) -> None:
        """
        Connect to Redis and start the background subscriber.
        Called once during application startup.
        """
        if not redis_url:
            logger.info("WebSocket pub/sub disabled: REDIS_URL not set")
            return

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
            )
            await self._redis.ping()

            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(_CHANNEL)

            self._subscriber_task = asyncio.create_task(
                self._subscriber_loop(),
                name="ws-pubsub-subscriber",
            )
            self._enabled = True
            logger.info("WebSocket pub/sub started (channel: %s)", _CHANNEL)

        except ImportError:
            logger.info("redis package not installed — WebSocket pub/sub disabled")
        except Exception as exc:
            logger.warning(
                "WebSocket pub/sub unavailable — falling back to in-process: %s", exc
            )

    async def stop(self) -> None:
        """Clean up subscriber task and Redis connections on shutdown."""
        if self._subscriber_task and not self._subscriber_task.done():
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(_CHANNEL)
                await self._pubsub.close()
            except Exception:
                pass

        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass

        self._enabled = False
        logger.info("WebSocket pub/sub stopped")

    async def publish(self, terminal_id: Optional[str], message: dict) -> None:
        """
        Publish a WebSocket event.

        If pub/sub is enabled: pushes to Redis channel → picked up by whichever
        worker owns the terminal's WebSocket connection.

        If pub/sub is disabled: falls back to direct in-process send (single-worker safe).
        """
        # Late import to avoid circular dependency with notifier
        from app.core.notifier import manager

        if not self._enabled or self._redis is None:
            # Fallback: direct in-process delivery
            if terminal_id:
                await manager.send(terminal_id, message)
            else:
                await manager.broadcast(message)
            return

        try:
            payload = json.dumps({
                "terminal_id": terminal_id,   # None → broadcast to all
                "message": message,
            })
            await self._redis.publish(_CHANNEL, payload)
        except Exception as exc:
            logger.warning("pub/sub publish failed, falling back: %s", exc)
            # Fallback to in-process on Redis error
            from app.core.notifier import manager
            if terminal_id:
                await manager.send(terminal_id, message)
            else:
                await manager.broadcast(message)

    async def _subscriber_loop(self) -> None:
        """
        Background task: listen for pub/sub messages and dispatch to local
        WebSocket connections in this worker.
        """
        from app.core.notifier import manager

        logger.debug("WebSocket pub/sub subscriber loop started")
        try:
            async for raw_message in self._pubsub.listen():
                if raw_message["type"] != "message":
                    continue

                try:
                    data = json.loads(raw_message["data"])
                    terminal_id = data.get("terminal_id")
                    message     = data.get("message", {})

                    if terminal_id:
                        await manager.send(terminal_id, message)
                    else:
                        await manager.broadcast(message)

                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    logger.warning("pub/sub message parse error: %s", exc)

        except asyncio.CancelledError:
            logger.debug("WebSocket pub/sub subscriber loop cancelled")
        except Exception as exc:
            logger.error("WebSocket pub/sub subscriber loop error: %s", exc, exc_info=True)


# Module-level singleton
ws_pubsub = WebSocketPubSub()
