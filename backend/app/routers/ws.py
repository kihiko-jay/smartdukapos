"""
WebSocket router (v4.3)

Auth change:
  - Terminals no longer pass ?token=<jwt> in the upgrade URL.
    JWTs in query strings leak into nginx access logs, browser history,
    and any reverse-proxy request log.
  - Instead: client calls POST /auth/ws-ticket immediately before opening
    the connection, gets a 30-second one-time ticket UUID, and passes
    ?ticket=<uuid>. The ticket is consumed on first use.
  - Heartbeat: server sends a ping every 30s. If the terminal doesn't respond
    within 10s, the connection is considered dead and cleaned up.
  - Race-condition-safe ConnectionManager used (see notifier.py).
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from app.core.notifier import manager
from app.database import SessionLocal
from app.models.employee import Employee
from app.routers.auth import consume_ws_ticket

logger = logging.getLogger("dukapos.ws")
router = APIRouter(tags=["WebSocket"])

HEARTBEAT_INTERVAL = 30   # seconds between server pings
HEARTBEAT_TIMEOUT  = 10   # seconds to wait for pong response


@router.websocket("/ws/pos/{terminal_id}")
async def pos_websocket(
    terminal_id: str,
    websocket:   WebSocket,
    ticket:      str = Query(..., description="One-time WS ticket from POST /auth/ws-ticket"),
):
    """
    Persistent WebSocket for a POS terminal.

    Auth: call POST /auth/ws-ticket to get a short-lived ticket, then:
      /ws/pos/T01?ticket=<uuid>

    The ticket is validated and consumed before the handshake completes.
    Never pass raw JWTs in WebSocket query strings.
    """
    # ── Auth: consume ticket before accepting ─────────────────────────────────
    employee_id = consume_ws_ticket(ticket)
    if employee_id is None:
        await websocket.close(code=4001)   # 4001 = unauthorized (custom code)
        logger.warning("WS: rejected invalid/expired ticket for terminal %s", terminal_id)
        return

    # Verify employee still exists and is active
    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.is_active == True,
        ).first()
        if not emp:
            await websocket.close(code=4001)
            logger.warning("WS: rejected inactive/unknown employee %s for terminal %s",
                           employee_id, terminal_id)
            return
    finally:
        db.close()

    # ── Accept and register ───────────────────────────────────────────────────
    await manager.connect(terminal_id, websocket)

    try:
        # ── Heartbeat + message loop ──────────────────────────────────────────
        while True:
            # Wait for a message OR heartbeat interval — whichever comes first
            try:
                msg = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                # Client sent a pong or other message — just continue
                logger.debug("WS message from %s: %s", terminal_id, msg[:50])

            except asyncio.TimeoutError:
                # Send heartbeat ping
                if websocket.client_state != WebSocketState.CONNECTED:
                    break
                try:
                    await websocket.send_json({"event": "ping"})
                    # Wait for pong
                    pong = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=HEARTBEAT_TIMEOUT,
                    )
                    logger.debug("WS heartbeat ok: %s", terminal_id)
                except (asyncio.TimeoutError, Exception):
                    logger.info("WS heartbeat timeout: %s — disconnecting", terminal_id)
                    break

    except WebSocketDisconnect:
        logger.info("WS client disconnected: %s", terminal_id)
    except Exception as exc:
        logger.error("WS error for terminal %s: %s", terminal_id, exc, exc_info=True)
    finally:
        await manager.disconnect(terminal_id)
