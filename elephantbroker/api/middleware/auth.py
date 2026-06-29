"""API authentication middleware — shared-secret token validation.

When ``auth_token`` is configured (non-empty), validates the
``X-EB-Auth-Token`` or ``Authorization: Bearer <token>`` header.
When ``auth_token`` is absent or empty, all requests pass through
(backward-compatible dev mode).

Health-check endpoints (``/health``, ``/health/ready``, ``/health/live``)
always pass regardless of token configuration.
"""
from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_AUTH_TOKEN_HEADER = "X-EB-Auth-Token"
_BEARER_PREFIX = "Bearer "

_HEALTH_PATHS: frozenset[str] = frozenset(
    {"/health", "/health/ready", "/health/live", "/health/"}
)


def _extract_token(request: Request, header_name: str) -> str | None:
    """Extract auth token from custom header or Authorization header."""
    value = request.headers.get(header_name)
    if value:
        return value
    auth = request.headers.get("Authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :]
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """API authentication via shared secret token.

    If ``auth_token`` is unset (``None`` or ``""``), all requests pass
    through — same as the original stub behavior.  When configured,
    requests without a matching token receive ``401 Unauthorized``.
    """

    def __init__(self, app, auth_token: str | None = None) -> None:  # type: ignore[override]
        super().__init__(app)
        self._token = auth_token or ""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._token:
            return await call_next(request)

        if request.url.path in _HEALTH_PATHS or request.url.path.startswith(
            "/health/"
        ):
            return await call_next(request)

        supplied = _extract_token(request, _AUTH_TOKEN_HEADER)
        if supplied is None:
            logger.warning(
                "Auth rejected (401): missing token | source=%s | path=%s",
                request.client,
                request.url.path,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authentication token"},
            )

        if not secrets.compare_digest(supplied, self._token):
            logger.warning(
                "Auth rejected (401): invalid token | source=%s | path=%s",
                request.client,
                request.url.path,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid authentication token"},
            )

        return await call_next(request)
