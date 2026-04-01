"""
In-process observability metrics (v4.0)

Lightweight counters and gauges — no external dependency required.
In production, export these to Prometheus via /metrics or push to Datadog.

Tracks:
  - HTTP request counts per route + status code
  - eTIMS submission outcomes (success/failure/retry)
  - M-PESA callback outcomes
  - Sync operations per entity
  - Active WebSocket connections
  - DB query latencies (P50/P95 via rolling window)

Usage:
    from app.core.metrics import metrics
    metrics.increment("etims.submitted")
    metrics.timing("db.query_ms", 42.3)
"""

import time
import logging
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

logger = logging.getLogger("dukapos.metrics")


class Metrics:
    def __init__(self):
        self._counters: dict[str, int]          = defaultdict(int)
        self._gauges:   dict[str, float]        = {}
        self._timings:  dict[str, deque]        = defaultdict(lambda: deque(maxlen=1000))
        self._lock = Lock()

    # ── Counter ───────────────────────────────────────────────────────────────
    def increment(self, key: str, value: int = 1, tags: Optional[dict] = None) -> None:
        tag_str = "".join(f",{k}={v}" for k, v in (tags or {}).items())
        full_key = f"{key}{tag_str}"
        with self._lock:
            self._counters[full_key] += value

    # ── Gauge ─────────────────────────────────────────────────────────────────
    def gauge(self, key: str, value: float) -> None:
        with self._lock:
            self._gauges[key] = value

    # ── Timing ────────────────────────────────────────────────────────────────
    def timing(self, key: str, ms: float) -> None:
        with self._lock:
            self._timings[key].append(ms)

    def percentile(self, key: str, p: float = 0.95) -> Optional[float]:
        with self._lock:
            data = sorted(self._timings.get(key, []))
        if not data:
            return None
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    # ── Snapshot ──────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            gauges   = dict(self._gauges)
            timing_stats = {
                k: {
                    "p50": self.percentile(k, 0.50),
                    "p95": self.percentile(k, 0.95),
                    "p99": self.percentile(k, 0.99),
                    "count": len(v),
                }
                for k, v in self._timings.items()
            }
        return {"counters": counters, "gauges": gauges, "timings": timing_stats}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timings.clear()


# Module-level singleton
metrics = Metrics()


# ── Context manager for timing code blocks ────────────────────────────────────
class timed:
    """
    Usage:
        with timed("db.get_product_ms", metrics):
            product = db.query(Product)...
    """
    def __init__(self, key: str, metrics_instance: Metrics = None):
        self.key     = key
        self.metrics = metrics_instance or metrics
        self._start  = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        self.metrics.timing(self.key, elapsed_ms)
