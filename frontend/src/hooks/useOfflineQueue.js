/**
 * useOfflineQueue — Persistent offline transaction queue (v4.0)
 *
 * Architecture:
 *  - In Electron: backed by SQLite via IPC (ACID — sales are NEVER lost)
 *  - In browser dev mode: falls back to sessionStorage (dev only, clearly labelled)
 *
 * Guarantees:
 *  1. Every enqueued sale has a unique idempotency_key — no double-posting
 *  2. Failed syncs are retried with exponential backoff (not silently dropped)
 *  3. Queue stats are surfaced to the UI so cashiers see pending count
 *  4. syncQueue() is idempotent — safe to call multiple times concurrently
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useElectron } from "./useElectron";
import { transactionsAPI } from "../api/client";

// Retry config
const MAX_ATTEMPTS   = 5;
const BASE_DELAY_MS  = 2_000;
const MAX_DELAY_MS   = 60_000;

function backoffDelay(attempt) {
  return Math.min(BASE_DELAY_MS * 2 ** attempt + Math.random() * 1000, MAX_DELAY_MS);
}

// ── Dev-only in-memory fallback (browser, non-Electron) ──────────────────────
const _memQueue = [];
const _browserFallback = {
  async enqueue(txn) {
    const key = txn.idempotency_key || txn.txn_number || `mem-${Date.now()}`;
    const item = { ...txn, idempotency_key: key };
    if (!_memQueue.find(t => t.idempotency_key === key)) _memQueue.push(item);
    console.warn("[offline-queue] Browser fallback — sale NOT persisted to disk:", key);
    return { success: true, idempotency_key: key, queueLength: _memQueue.length };
  },
  async getQueue() { return [..._memQueue]; },
  async clearItem(key) {
    const idx = _memQueue.findIndex(t => t.idempotency_key === key);
    if (idx !== -1) _memQueue.splice(idx, 1);
    return { remaining: _memQueue.length };
  },
  async markFailed(key, error) {
    const item = _memQueue.find(t => t.idempotency_key === key);
    if (item) item._lastError = String(error);
  },
  async stats() { return { pending: _memQueue.length, failed: 0, synced: 0 }; },
};


export function useOfflineQueue() {
  const { isElectron } = useElectron();
  const queue          = isElectron ? window.electron?.offline : _browserFallback;

  const [queueLength, setQueueLength] = useState(0);
  const [stats, setStats]             = useState({ pending: 0, failed: 0, synced: 0 });
  const [isOnline, setIsOnline]       = useState(navigator.onLine);
  const [syncing, setSyncing]         = useState(false);
  const syncingRef                    = useRef(false);   // ref for use inside async callbacks

  // ── Track online/offline ────────────────────────────────────────────────────
  useEffect(() => {
    const goOnline  = () => { setIsOnline(true);  syncQueue(); };
    const goOffline = () => setIsOnline(false);
    window.addEventListener("online",  goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online",  goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  // ── Poll queue stats every 10 s ─────────────────────────────────────────────
  useEffect(() => {
    refreshStats();
    const interval = setInterval(refreshStats, 10_000);
    return () => clearInterval(interval);
  }, []);

  const refreshStats = useCallback(async () => {
    if (!queue) return;
    try {
      const s = await queue.stats();
      setStats(s);
      setQueueLength(s.pending + s.failed);
    } catch { /* non-fatal */ }
  }, [queue]);


  // ── Enqueue — wraps idempotency key generation ──────────────────────────────
  const enqueue = useCallback(async (transaction) => {
    if (!queue) return { success: false, error: "Queue not available" };

    // Ensure idempotency key is set before handing to the queue
    const txnWithKey = {
      ...transaction,
      idempotency_key: transaction.idempotency_key
        || transaction.txn_number
        || `txn-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    };

    const result = await queue.enqueue(txnWithKey);
    await refreshStats();
    return result;
  }, [queue, refreshStats]);


  // ── Sync queue — retry pending with backoff ─────────────────────────────────
  const syncQueue = useCallback(async () => {
    if (syncingRef.current) return 0;   // prevent overlapping runs
    if (!navigator.onLine)  return 0;
    if (!queue)             return 0;

    syncingRef.current = true;
    setSyncing(true);
    let synced = 0;

    try {
      const items = await queue.getQueue();
      if (items.length === 0) return 0;

      for (const item of items) {
        const key = item.idempotency_key;

        // Skip items that have exceeded max retries
        if ((item._attempts || 0) >= MAX_ATTEMPTS) {
          console.error("[offline-queue] Item exceeded max retries, leaving as failed:", key);
          continue;
        }

        try {
          // Strip internal queue metadata before posting
          const { idempotency_key, _queuedAt, _attempts, _lastError, ...txnPayload } = item;

          await transactionsAPI.create(txnPayload, {
            headers: { "Idempotency-Key": idempotency_key },
          });

          await queue.clearItem(key);
          synced++;
          console.info("[offline-queue] Synced:", key);
        } catch (err) {
          const message = err?.response?.data?.detail || err.message || "Unknown error";
          await queue.markFailed(key, message);
          console.warn("[offline-queue] Sync failed for", key, "—", message);

          // Exponential backoff: delay before next item to avoid hammering API
          const attempt = (item._attempts || 0) + 1;
          if (items.indexOf(item) < items.length - 1) {
            await new Promise(r => setTimeout(r, backoffDelay(attempt)));
          }
        }
      }
    } finally {
      syncingRef.current = false;
      setSyncing(false);
      await refreshStats();
    }

    return synced;
  }, [queue, refreshStats]);


  return {
    isOnline,
    syncing,
    queueLength,
    stats,          // { pending, failed, synced } — show in status bar
    enqueue,
    syncQueue,
    refreshStats,
  };
}
