"""
WebSocket connection manager (v4.0)

Race condition fixes:
  1. asyncio.Lock per terminal prevents concurrent sends from corrupting the
     WebSocket frame stream. Without this, two coroutines sending simultaneously
     (e.g. M-PESA callback + low-stock alert) produce a broken frame.
  2. Connection replacement: if a terminal reconnects (Electron restart), the
     old socket is explicitly closed before the new one is registered.
  3. send() queues messages while the lock is held — no message is silently
     dropped due to a concurrent send.
  4. Dead connection detection: WebSocketDisconnect during send triggers clean
     removal from the registry.

Observability:
  - Every connect/disconnect/send/error is logged with terminal_id and
    a running connection count.
"""

import asyncio
import logging
from typing import Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger("dukapos.ws")


class _TerminalConnection:
    """Wraps a WebSocket with a per-connection send lock."""

    def __init__(self, terminal_id: str, ws: WebSocket):
        self.terminal_id = terminal_id
        self.ws          = ws
        self._lock       = asyncio.Lock()

    async def send(self, message: dict) -> bool:
        """
        Send a JSON message. Returns True on success, False on failure.
        The lock ensures only one send is in-flight per terminal at a time.
        """
        async with self._lock:
            if self.ws.client_state != WebSocketState.CONNECTED:
                return False
            try:
                await self.ws.send_json(message)
                return True
            except WebSocketDisconnect:
                return False
            except Exception as exc:
                logger.warning("WS send error for terminal %s: %s", self.terminal_id, exc)
                return False

    async def close(self):
        try:
            if self.ws.client_state == WebSocketState.CONNECTED:
                await self.ws.close(code=1001)
        except Exception:
            pass


class ConnectionManager:
    """
    Registry of active POS terminal WebSocket connections.

    Thread-safe via asyncio (single-threaded event loop).
    NOT safe for multi-process deployments — use Redis pub/sub if you scale
    to multiple uvicorn workers.
    """

    def __init__(self):
        self._connections: dict[str, _TerminalConnection] = {}
        self._registry_lock = asyncio.Lock()

    async def connect(self, terminal_id: str, ws: WebSocket) -> None:
        await ws.accept()

        async with self._registry_lock:
            # Close any existing connection for this terminal (reconnect case)
            existing = self._connections.get(terminal_id)
            if existing:
                logger.info("Terminal %s reconnecting — closing old socket", terminal_id)
                await existing.close()

            self._connections[terminal_id] = _TerminalConnection(terminal_id, ws)

        logger.info("Terminal connected: %s (total: %d)", terminal_id, len(self._connections))

    async def disconnect(self, terminal_id: str) -> None:
        async with self._registry_lock:
            conn = self._connections.pop(terminal_id, None)
            if conn:
                await conn.close()
        logger.info("Terminal disconnected: %s (total: %d)", terminal_id, len(self._connections))

    async def send(self, terminal_id: str, message: dict) -> bool:
        conn = self._connections.get(terminal_id)
        if not conn:
            logger.debug("Terminal %s not connected — event dropped: %s",
                         terminal_id, message.get("event"))
            return False

        ok = await conn.send(message)
        if not ok:
            # Dead connection — clean up
            async with self._registry_lock:
                self._connections.pop(terminal_id, None)
            logger.info("Dead connection removed: %s", terminal_id)
        return ok

    async def broadcast(self, message: dict) -> int:
        """Send to all connected terminals. Returns count of successful sends."""
        terminal_ids = list(self._connections.keys())
        results = await asyncio.gather(
            *[self.send(tid, message) for tid in terminal_ids],
            return_exceptions=True,
        )
        sent = sum(1 for r in results if r is True)
        logger.debug("Broadcast sent to %d/%d terminals", sent, len(terminal_ids))
        return sent

    @property
    def connected_terminals(self) -> list[str]:
        return list(self._connections.keys())


# Module-level singleton
manager = ConnectionManager()


# ── Typed event helpers ────────────────────────────────────────────────────────

async def notify_mpesa_confirmed(
    terminal_id: Optional[str],
    txn_number: str,
    mpesa_ref: str,
) -> None:
    """
    Push mpesa_confirmed event to the terminal that initiated the payment.

    Routes through Redis pub/sub when available (multi-worker safe).
    Falls back to direct in-process send otherwise.
    """
    if not terminal_id:
        logger.debug("notify_mpesa_confirmed: no terminal_id for txn %s", txn_number)
        return
    from app.core.pubsub import ws_pubsub
    await ws_pubsub.publish(terminal_id, {
        "event":      "mpesa_confirmed",
        "txn_number": txn_number,
        "mpesa_ref":  mpesa_ref,
    })


async def notify_mpesa_failed(
    terminal_id: Optional[str],
    txn_number: str,
    result_code: int,
) -> None:
    """
    Push mpesa_failed event so the cashier can retry or switch payment method.
    Routes through Redis pub/sub when available.
    """
    if not terminal_id:
        return
    messages = {
        1032: "Payment cancelled by customer",
        1037: "Request timeout — customer did not respond",
        2001: "Incorrect PIN entered",
    }
    from app.core.pubsub import ws_pubsub
    await ws_pubsub.publish(terminal_id, {
        "event":       "mpesa_failed",
        "txn_number":  txn_number,
        "result_code": result_code,
        "message":     messages.get(result_code, f"Payment failed (code {result_code})"),
    })


async def notify_low_stock(product_name: str, sku: str, qty: int) -> int:
    """Broadcast low-stock alert to all connected terminals. Routes through pub/sub."""
    from app.core.pubsub import ws_pubsub
    await ws_pubsub.publish(None, {   # None → broadcast
        "event":        "low_stock",
        "product_name": product_name,
        "sku":          sku,
        "qty":          qty,
    })
    return len(manager.connected_terminals)
