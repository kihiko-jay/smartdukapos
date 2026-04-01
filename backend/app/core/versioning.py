"""
API Versioning (v4.0)

Strategy: URL prefix versioning (/api/v1/, /api/v2/)
  - Current stable: v1
  - All v1 routes remain available under /api/v1/
  - X-API-Version response header on every response tells clients the
    running version so they can detect when they're on a stale build
  - X-Deprecation-Notice header warns clients on v1 endpoints that have
    a v2 replacement (set per-route with the deprecation_notice dict below)
  - 406 returned if client sends Accept: application/vnd.dukapos.v99+json
    (future explicit content negotiation support)

Usage in main.py:
    app.add_middleware(APIVersionMiddleware)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CURRENT_VERSION = "v1"  # URL prefix stays v1; app version is 4.0
SUPPORTED_VERSIONS = {"v1"}

# Routes that have been superseded — warn clients proactively
# Format: path_prefix → notice message
DEPRECATION_NOTICES: dict[str, str] = {
    # Example for future use:
    # "/api/v1/reports/z-tape": "Migrate to /api/v2/reports/z-tape by 2026-01-01",
}


class APIVersionMiddleware(BaseHTTPMiddleware):
    """
    Adds version metadata headers to every API response.

    Headers added:
      X-API-Version: v1                      — running API version
      X-Deprecation-Notice: <message>        — only on deprecated routes
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path

        # Only annotate /api/* paths
        if not path.startswith("/api/"):
            return response

        response.headers["X-API-Version"] = CURRENT_VERSION

        # Check if this path has a deprecation notice
        for prefix, notice in DEPRECATION_NOTICES.items():
            if path.startswith(prefix):
                response.headers["X-Deprecation-Notice"] = notice
                break

        return response
