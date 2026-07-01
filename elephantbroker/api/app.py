"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from elephantbroker.api.middleware.auth import AuthMiddleware
from elephantbroker.api.middleware.errors import error_handler_middleware
from elephantbroker.api.middleware.gateway import GatewayIdentityMiddleware
from elephantbroker.api.routes import (
    actors,
    admin,
    artifacts,
    auth,
    claims,
    consolidation,
    context,
    dashboard,
    goals,
    guards,
    health,
    memory,
    metrics,
    procedures,
    profiles,
    rerank,
    sessions,
    stats,
    trace,
    working_set,
)
from elephantbroker.runtime.container import RuntimeContainer


def create_app(container: RuntimeContainer) -> FastAPI:
    """Create FastAPI app with all routes and middleware.

    Accepts a pre-built RuntimeContainer so tests can inject mocked adapters.

    Takes ownership of *container*; ``await container.close()`` is called on
    app shutdown via the lifespan context manager. Do not pass the same
    container to multiple ``create_app()`` calls.
    """
    # Lifespan: yield on startup; close container on shutdown.
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await container.close()

    app = FastAPI(
        title="ElephantBroker",
        version="0.4.0",
        description="Unified Cognitive Runtime",
        lifespan=lifespan,
    )
    app.state.container = container

    # Middleware (applied in reverse order — gateway runs first)
    app.add_middleware(AuthMiddleware)
    # Middleware fallback must equal the container's gateway_id exactly. Bucket A
    # (commit d850186) changed GatewayConfig.gateway_id default from "local" to ""
    # and added the EB_ALLOW_DEFAULT_GATEWAY_ID opt-out. The prior shim here
    # re-fabricated "local" as the middleware fallback when config was empty,
    # which caused a store/lookup mismatch: write paths stamped DataPoints with
    # gateway_id="local" (from the middleware default) while read paths used the
    # engine's construction-time gateway_id="" (from the config), and the strict
    # Cypher filter rejected the mismatch. Passing the config value through
    # unchanged keeps both sides byte-identical.
    default_gw = ""
    if hasattr(container, "config") and container.config and hasattr(container.config, "gateway"):
        default_gw = container.config.gateway.gateway_id
    app.add_middleware(GatewayIdentityMiddleware, default_gateway_id=default_gw)

    # Phase 11 dashboard auth (SuperTokens + CORS). Only wired when explicitly
    # enabled so pre-Phase-11 deployments and existing tests keep the
    # no-enforcement behaviour. Everything below degrades gracefully when the
    # optional ``supertokens_python`` dependency is not installed.
    dashboard_auth_cfg = None
    if hasattr(container, "config") and container.config:
        dashboard_auth_cfg = getattr(container.config, "dashboard_auth", None)

    if dashboard_auth_cfg is not None and getattr(dashboard_auth_cfg, "enabled", False):
        # Initialize the SuperTokens SDK (emailpassword + session + usermetadata).
        # init_supertokens is non-fatal: it returns False and logs a warning when
        # the SDK is unavailable, leaving session auth simply inactive.
        try:
            from elephantbroker.api.auth.supertokens_config import (
                get_supertokens_middleware,
                init_supertokens,
            )

            # CRITICAL: only wire the SuperTokens ASGI middleware if init
            # actually SUCCEEDED. init_supertokens() returns False (and logs a
            # warning) when the SDK is unavailable OR the config is invalid
            # (e.g. missing/placeholder domain). If we add the ST middleware
            # after a failed init, its __call__ raises
            # `GeneralError: Initialisation not done` on EVERY request → HTTP
            # 500 on all routes, including auth-exempt ones like /guards. Gating
            # on the return value keeps routes passing through with anonymous
            # identity (pre-Phase-11 behaviour) whenever ST isn't fully up.
            if init_supertokens(dashboard_auth_cfg):
                # SuperTokens ASGI middleware handles the auto-generated /auth/*
                # routes and token refresh. Added here so — after the
                # reverse-order wrapping — it runs AFTER gateway/CORS but BEFORE
                # AuthMiddleware.
                st_mw = get_supertokens_middleware()
                if st_mw is not None:
                    app.add_middleware(st_mw)
            else:
                logging.getLogger("elephantbroker.api").warning(
                    "SuperTokens init did not succeed — ST middleware NOT wired; "
                    "session auth inactive, routes pass through with anonymous "
                    "identity."
                )
        except Exception as exc:  # pragma: no cover - import/init safety net
            logging.getLogger("elephantbroker.api").warning(
                "SuperTokens wiring skipped: %s", exc
            )

    app.middleware("http")(error_handler_middleware)

    # CORS for the dashboard origin. Outermost middleware (added last) so
    # cross-origin preflight (OPTIONS) is answered before auth runs, and CORS
    # headers are attached to every response including errors. Credentialed
    # requests are required because SuperTokens uses cookies. Only added when
    # dashboard auth is enabled — same-origin prod (/ui/*) needs no CORS.
    if dashboard_auth_cfg is not None and getattr(dashboard_auth_cfg, "enabled", False):
        from fastapi.middleware.cors import CORSMiddleware

        cors_headers = ["Content-Type", "Authorization"]
        try:
            from supertokens_python import get_all_cors_headers

            cors_headers = ["Content-Type"] + get_all_cors_headers()
        except Exception:
            pass

        website_domain = getattr(dashboard_auth_cfg, "website_domain", "") or ""
        allow_origins = [website_domain] if website_domain else []
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=cors_headers,
        )

    # Log validation errors with full detail (helps debug 422s from plugins)
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        logger = logging.getLogger("elephantbroker.api")
        logger.warning("Validation error on %s %s: %s", request.method, request.url.path, exc.errors())
        # Sanitize errors to remove non-serializable objects (e.g., ValueError instances in ctx)
        errors = []
        for err in exc.errors():
            sanitized = {k: v for k, v in err.items() if k != "ctx"}
            # If ctx exists and has an 'error' field with an exception, extract its string representation
            if "ctx" in err and isinstance(err["ctx"], dict) and "error" in err["ctx"]:
                error_obj = err["ctx"]["error"]
                sanitized["ctx"] = {"error": str(error_obj)} if not isinstance(error_obj, str) else err["ctx"]
            errors.append(sanitized)
        return JSONResponse(status_code=422, content={"detail": errors})

    # OTEL instrumentation (additive, no-op without endpoint)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass

    # Routes
    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(memory.router, prefix="/memory", tags=["memory"])
    app.include_router(context.router, prefix="/context", tags=["context"])
    app.include_router(actors.router, prefix="/actors", tags=["actors"])
    app.include_router(goals.router, prefix="/goals", tags=["goals"])
    app.include_router(procedures.router, prefix="/procedures", tags=["procedures"])
    app.include_router(claims.router, prefix="/claims", tags=["claims"])
    app.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"])
    app.include_router(profiles.router, prefix="/profiles", tags=["profiles"])
    app.include_router(trace.router, prefix="/trace", tags=["trace"])
    app.include_router(stats.router, prefix="/stats", tags=["stats"])
    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(working_set.router, prefix="/working-set", tags=["working-set"])
    app.include_router(rerank.router, prefix="/rerank", tags=["rerank"])
    app.include_router(guards.router, tags=["guards"])
    app.include_router(consolidation.router, prefix="/consolidation", tags=["consolidation"])
    app.include_router(metrics.router, tags=["metrics"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    # Phase 11 routers self-declare their prefixes (/auth, /dashboard) on the
    # APIRouter, so they are included with no additional prefix (like guards).
    app.include_router(auth.router, tags=["auth"])
    app.include_router(dashboard.router, tags=["dashboard"])

    # Serve the built dashboard bundle same-origin at /ui in production (AD-11).
    # Off unless EB_DASHBOARD_STATIC_DIR / dashboard_auth.static_dir points at a
    # real directory — no CORS needed for this path.
    static_dir = getattr(dashboard_auth_cfg, "static_dir", "") if dashboard_auth_cfg else ""
    if static_dir:
        import os

        if os.path.isdir(static_dir):
            from fastapi.staticfiles import StaticFiles

            app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")
        else:
            logging.getLogger("elephantbroker.api").warning(
                "EB_DASHBOARD_STATIC_DIR=%r is not a directory — /ui not served",
                static_dir,
            )

    return app
