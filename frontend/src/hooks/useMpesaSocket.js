/**
 * useMpesaSocket (v4.3)
 *
 * Auth change vs v4.0:
 *  - No longer passes ?token=<jwt> in the WebSocket upgrade URL.
 *    JWTs in query strings appear in nginx access logs, browser history,
 *    and any reverse-proxy request log — a real credential-leak vector.
 *  - Instead: calls POST /auth/ws-ticket immediately before connecting.
 *    The server returns a 30-second one-time UUID ticket. The WS URL
 *    carries ?ticket=<uuid>, which is worthless after first use.
 *
 * Other fixes retained from v4.0:
 *  2. RECONNECT DEDUP via connectingRef guard
 *  3. EXPONENTIAL BACKOFF on reconnect
 *  4. CLEANUP cancels pending timers on unmount
 *  5. HEARTBEAT PONG responds to server ping events
 *  6. MPESA FAILED event surfaces reason to cashier
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { authAPI } from "../api/client";

const MAX_RETRIES   = 10;
const BASE_DELAY_MS = 1_000;
const MAX_DELAY_MS  = 30_000;

function backoffDelay(attempt) {
  return Math.min(BASE_DELAY_MS * 2 ** attempt + Math.random() * 500, MAX_DELAY_MS);
}

/**
 * @param {string|null}   terminalId
 * @param {Function}      onPaymentConfirmed  - (txnNumber, mpesaRef) => void
 * @param {Function}      [onPaymentFailed]   - (txnNumber, resultCode, message) => void
 */
export function useMpesaSocket(terminalId, onPaymentConfirmed, onPaymentFailed) {
  const wsRef         = useRef(null);
  const retryCount    = useRef(0);
  const retryTimerRef = useRef(null);
  const mountedRef    = useRef(true);
  const connectingRef = useRef(false);

  const [connected, setConnected] = useState(false);

  const connect = useCallback(async () => {
    if (!terminalId)           return;
    if (!mountedRef.current)   return;
    if (connectingRef.current) return;

    connectingRef.current = true;

    // Fetch a short-lived one-time ticket — never put JWTs in WS query strings
    let ticket = "";
    try {
      const res = await authAPI.wsTicket();
      ticket = res?.ticket || "";
    } catch {
      // Non-fatal: server will reject with code 4001 and we won't retry
    }

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const host     = window.location.host || "localhost:8000";
    const url      = `${protocol}://${host}/ws/pos/${terminalId}?ticket=${encodeURIComponent(ticket)}`;

    const socket = new WebSocket(url);

    socket.onopen = () => {
      if (!mountedRef.current) { socket.close(); return; }
      setConnected(true);
      retryCount.current    = 0;
      connectingRef.current = false;
    };

    socket.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        switch (data.event) {
          case "mpesa_confirmed":
            onPaymentConfirmed?.(data.txn_number, data.mpesa_ref);
            break;
          case "mpesa_failed":
            onPaymentFailed?.(data.txn_number, data.result_code, data.message);
            break;
          case "ping":
            if (socket.readyState === WebSocket.OPEN) socket.send("pong");
            break;
          default:
            break;
        }
      } catch { /* Malformed message — ignore */ }
    };

    socket.onclose = (event) => {
      setConnected(false);
      connectingRef.current = false;
      if (!mountedRef.current) return;

      // 4001 = auth rejected (bad/expired ticket) — don't retry
      if (event.code === 4001) {
        console.warn("[ws] Connection rejected (auth). Will not retry.");
        return;
      }
      if (retryCount.current >= MAX_RETRIES) {
        console.warn(`[ws] Max retries (${MAX_RETRIES}) reached for terminal ${terminalId}`);
        return;
      }

      const delay = backoffDelay(retryCount.current);
      retryCount.current++;
      console.info(`[ws] Reconnecting in ${Math.round(delay / 1000)}s (attempt ${retryCount.current})`);
      retryTimerRef.current = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      connectingRef.current = false;
      socket.close();
    };

    wsRef.current = socket;
  }, [terminalId, onPaymentConfirmed, onPaymentFailed]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(retryTimerRef.current);
      wsRef.current?.close(1000, "component unmounted");
    };
  }, [connect]);

  return { connected };
}
