"""
HTTP middleware: request logging, error tracking, correlation IDs.

Adds X-Request-ID to every response so distributed traces can be correlated
across backend logs, sync agent logs, and frontend error reports.
"""

import time
import uuid
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("dukapos.http")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every inbound request and its response.

    Captures:
      - method, path, query string
      - client IP
      - response status code
      - latency in ms
      - correlation ID (X-Request-ID)

    Excludes /health from logging to avoid noise.
    """

    SKIP_PATHS = {"/health", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip noisy health-check pings
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start  = time.perf_counter()

        logger.info(
            "→ %s %s",
            request.method,
            request.url.path,
            extra={
                "req_id":     req_id,
                "method":     request.method,
                "path":       request.url.path,
                "query":      str(request.query_params),
                "client_ip":  request.client.host if request.client else "unknown",
            },
        )

        try:
            response: Response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.error(
                "Unhandled exception during %s %s",
                request.method, request.url.path,
                extra={"req_id": req_id, "duration_ms": duration_ms},
                exc_info=True,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        logger.log(
            level,
            "← %s %s %d  %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "req_id":      req_id,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["X-Request-ID"] = req_id
        return response
