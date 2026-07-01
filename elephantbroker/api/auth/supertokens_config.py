"""SuperTokens SDK initialization for the dashboard auth layer (Phase 11).

Initializes SuperTokens with exactly three recipes — ``emailpassword`` +
``session`` + ``usermetadata`` — using ``framework="fastapi"`` and
``api_base_path="/auth"``. This provides the auto-generated ``/auth/*`` routes
(signin, signup, signout, session refresh) and the ``get_session`` dependency
used by ``AuthMiddleware``.

The ``supertokens_python`` dependency is HEAVY and OPTIONAL: it is lazy-imported
inside ``init_supertokens`` and every call site guards against ImportError, so
this module imports cleanly when the dependency is not installed. When
SuperTokens is unavailable or disabled, the session auth path is simply
inactive and callers fall through to API-key / gateway-identity auth.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level flag so callers (middleware, routes) can cheaply check whether
# the SDK was successfully initialized before attempting session operations.
_SUPERTOKENS_INITIALIZED = False


def supertokens_available() -> bool:
    """Return True if the ``supertokens_python`` package is importable."""
    try:
        import supertokens_python  # noqa: F401
    except Exception:
        return False
    return True


def is_initialized() -> bool:
    """Return True if ``init_supertokens`` completed successfully this process."""
    return _SUPERTOKENS_INITIALIZED


def init_supertokens(config: Any) -> bool:
    """Initialize the SuperTokens SDK. Idempotent-safe and non-fatal.

    ``config`` is duck-typed (a ``DashboardAuthConfig``-like object) and must
    expose: ``core_uri``, ``api_domain``, ``website_domain`` and, optionally,
    ``cookie_secure`` / ``cookie_same_site``.

    Returns ``True`` on success, ``False`` when SuperTokens is unavailable or
    initialization failed — the app continues to run with session auth disabled.
    """
    global _SUPERTOKENS_INITIALIZED

    try:
        from supertokens_python import (
            InputAppInfo,
            SupertokensConfig,
            init,
        )
        from supertokens_python.recipe import (
            emailpassword,
            session,
            usermetadata,
        )
    except Exception as exc:
        logger.warning(
            "SuperTokens SDK not installed — dashboard session auth disabled (%s)",
            exc,
        )
        _SUPERTOKENS_INITIALIZED = False
        return False

    cookie_secure = bool(getattr(config, "cookie_secure", False))
    cookie_same_site = getattr(config, "cookie_same_site", "lax") or "lax"

    try:
        init(
            app_info=InputAppInfo(
                app_name="ElephantBroker Dashboard",
                api_domain=getattr(config, "api_domain", "http://localhost:8420"),
                website_domain=getattr(
                    config, "website_domain", "http://localhost:5173"
                ),
                api_base_path="/auth",
            ),
            supertokens_config=SupertokensConfig(
                connection_uri=getattr(config, "core_uri", "http://localhost:3567"),
            ),
            framework="fastapi",
            recipe_list=[
                emailpassword.init(),
                session.init(
                    cookie_secure=cookie_secure,
                    cookie_same_site=cookie_same_site,
                ),
                usermetadata.init(),
            ],
            mode="asgi",
        )
    except Exception as exc:
        logger.warning("SuperTokens initialization failed — session auth disabled: %s", exc)
        _SUPERTOKENS_INITIALIZED = False
        return False

    _SUPERTOKENS_INITIALIZED = True
    logger.info("SuperTokens initialized (core=%s)", getattr(config, "core_uri", "?"))
    return True


def get_supertokens_middleware() -> Any | None:
    """Return the SuperTokens ASGI middleware class, or ``None`` if unavailable.

    The Wire agent adds this to the FastAPI app when dashboard auth is enabled::

        mw = get_supertokens_middleware()
        if mw is not None:
            app.add_middleware(mw)
    """
    try:
        from supertokens_python.framework.fastapi import get_middleware

        return get_middleware()
    except Exception as exc:
        logger.debug("SuperTokens middleware unavailable: %s", exc)
        return None
