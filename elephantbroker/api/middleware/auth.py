"""Unified authentication middleware (Phase 11).

Replaces the Phase 7 no-op placeholder. On every request it resolves the caller
into an :class:`~elephantbroker.api.auth.identity.AuthIdentity` (SuperTokens
session cookie → ``X-EB-API-Key`` → ``X-EB-Agent-Key`` gateway identity →
legacy ``X-EB-Actor-Id`` → anonymous) and stamps it on ``request.state``.

Design notes
------------
* Runs AFTER :class:`GatewayIdentityMiddleware`, so ``request.state.gateway_id``
  is already validated and tenant-checked. gateway_id is never taken from the
  API-key row (only asserted equal to the request's).
* The identity is stamped on BOTH ``request.state.identity`` (name used by the
  ``require_authority`` dependency and Phase 11 dashboard routes) and
  ``request.state.auth_identity`` (name used by the plan's handlers).
* ``TryRefreshTokenError`` → HTTP 401 ``{"error": "Token expired"}``.
* Route-class enforcement (``UNPROTECTED`` / ``AUTH_REQUIRED`` / ``FLEXIBLE``)
  is applied ONLY when dashboard auth is enabled. When auth is disabled (the
  default, backward-compatible mode) the middleware never blocks — it only
  stamps identity — so pre-Phase-11 tests and gateway-identity runtime traffic
  keep working unchanged.
* Everything degrades gracefully: if SuperTokens is not installed/configured, or
  the container has no ``api_key_store``, those paths are simply skipped.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve the request principal and (when enabled) enforce route classes."""

    # Route prefixes that never require authentication.
    UNPROTECTED: tuple[str, ...] = (
        "/health",
        "/metrics",
        "/auth",
        "/docs",
        "/openapi.json",
        "/redoc",
    )
    # Route prefixes that require some authenticated identity.
    AUTH_REQUIRED: tuple[str, ...] = ("/admin", "/dashboard")
    # Route prefixes that accept any auth method (including gateway identity).
    FLEXIBLE: tuple[str, ...] = ("/trace",)
    # Static/navigable prefixes reached by TOP-LEVEL browser navigation rather
    # than the SuperTokens-patched ``fetch`` — there is no interceptor on these
    # requests to catch a 401 and trigger a session refresh. Hard-401'ing them on
    # ``TryRefreshTokenError`` returns raw JSON to the browser and the dashboard
    # SPA never boots, so its own recovery logic (auto-refresh, redirect-to-login,
    # signOut) can never run. These paths must therefore load anonymously on an
    # expired token so the SPA can mount and refresh itself. ``/auth/*`` is
    # DELIBERATELY excluded: it is a custom fetch-driven route whose 401 the
    # client is expected to catch and refresh — serving it anonymously would
    # poison the frontend identity cache.
    STATIC_NAVIGABLE: tuple[str, ...] = (
        "/ui",
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    )

    def __init__(self, app, config=None) -> None:  # type: ignore[override]
        super().__init__(app)
        # Config is optional — the middleware also reads it live from the
        # container at dispatch time so it works regardless of how it is wired.
        self._config = config

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        container = getattr(request.app.state, "container", None)

        try:
            from elephantbroker.api.auth.identity import (
                AuthIdentity,
                resolve_identity,
            )
        except Exception as exc:  # pragma: no cover - import safety net
            logger.debug("auth identity module unavailable: %s", exc)
            return await call_next(request)

        try:
            from supertokens_python.recipe.session.exceptions import (
                TryRefreshTokenError,
            )
        except Exception:
            TryRefreshTokenError = None  # type: ignore[assignment]

        # Compute path up-front so it is available in the except handler below.
        path = request.url.path

        identity: AuthIdentity
        try:
            if container is not None:
                identity = await resolve_identity(request, container)
            else:
                identity = AuthIdentity()
        except Exception as exc:
            # The only expected raise from resolve_identity is the token-refresh
            # signal — surface it as 401 so the client refreshes its session.
            if TryRefreshTokenError is not None and isinstance(
                exc, TryRefreshTokenError
            ):
                # Static/navigable paths (see STATIC_NAVIGABLE) are fetched by a
                # top-level browser navigation with no fetch interceptor to catch
                # a 401 and refresh — so hard-401'ing them stops the SPA from ever
                # loading and running its own recovery. Serve them anonymously on
                # an expired token so the SPA can boot and refresh itself. All
                # other paths (notably custom fetch-driven /auth/* routes) keep
                # returning 401 so the client refreshes its session.
                if any(path.startswith(p) for p in self.STATIC_NAVIGABLE):
                    identity = AuthIdentity()
                else:
                    return JSONResponse(
                        status_code=401, content={"error": "Token expired"}
                    )
            else:
                logger.warning("identity resolution error: %s", exc)
                identity = AuthIdentity()

        request.state.identity = identity
        request.state.auth_identity = identity

        # Route-class enforcement only when dashboard auth is enabled.
        if self._auth_enabled(container):
            if not any(path.startswith(p) for p in self.UNPROTECTED):
                if any(path.startswith(p) for p in self.AUTH_REQUIRED):
                    if not identity.is_authenticated and not identity.is_bootstrap:
                        return JSONResponse(
                            status_code=401,
                            content={"error": "Authentication required"},
                        )

        return await call_next(request)

    def _auth_enabled(self, container) -> bool:
        """True when dashboard auth enforcement is switched on."""
        cfg = self._config
        if cfg is None and container is not None:
            cfg = getattr(getattr(container, "config", None), "dashboard_auth", None)
        return bool(getattr(cfg, "enabled", False))
