"""Tests for error handler and auth middleware."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest

from elephantbroker.api.middleware.auth import AuthMiddleware
from elephantbroker.api.middleware.errors import error_handler_middleware


def _make_request():
    scope = {"type": "http", "method": "GET", "path": "/test", "headers": []}
    return StarletteRequest(scope)


class TestErrorHandlerMiddleware:
    async def test_success_passthrough(self):
        request = _make_request()
        expected = Response(content="ok")
        call_next = AsyncMock(return_value=expected)
        result = await error_handler_middleware(request, call_next)
        assert result is expected

    async def test_key_error_returns_404(self):
        request = _make_request()
        call_next = AsyncMock(side_effect=KeyError("missing"))
        result = await error_handler_middleware(request, call_next)
        assert result.status_code == 404
        assert isinstance(result, JSONResponse)

    async def test_value_error_returns_422(self):
        request = _make_request()
        call_next = AsyncMock(side_effect=ValueError("bad"))
        result = await error_handler_middleware(request, call_next)
        assert result.status_code == 422

    async def test_generic_exception_returns_500(self, caplog):
        """G2 extension: unhandled exception returns 500 AND logs at ERROR with exc_info."""
        request = _make_request()
        call_next = AsyncMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.ERROR, logger="elephantbroker.api.errors"):
            result = await error_handler_middleware(request, call_next)
        assert result.status_code == 500
        assert "Unhandled error on GET /test: boom" in caplog.text

    async def test_response_content_type_json(self):
        request = _make_request()
        call_next = AsyncMock(side_effect=KeyError("x"))
        result = await error_handler_middleware(request, call_next)
        assert result.media_type == "application/json"

    async def test_request_validation_error_returns_422_with_warning_log(self, caplog):
        """G1 (#305): RequestValidationError returns 422 AND emits a WARNING log with full
        request method + path + error details. Pins the debugging affordance for 422s
        originating from plugin schema mismatches.
        """
        request = _make_request()
        call_next = AsyncMock(side_effect=RequestValidationError([
            {"loc": ("body", "x"), "msg": "required", "type": "missing"},
        ]))
        with caplog.at_level(logging.WARNING, logger="elephantbroker.api.errors"):
            result = await error_handler_middleware(request, call_next)
        assert result.status_code == 422
        assert "Validation error on GET /test" in caplog.text

    async def test_permission_error_maps_to_403_post_R2P5_fix(self):
        """G6 FLIPPED (#1170 RESOLVED — R2-P5): the middleware now maps
        ``PermissionError`` to HTTP 403 in its fallback path, matching
        the route-level handlers (memory.py promote_scope/promote_class/
        update/delete) that already explicitly catch PermissionError and
        return 403.

        Pre-R2-P5 the middleware fell through to the generic Exception
        handler and returned 500 — pinned in TF-FN-014 G6 as documented
        gap. The fix adds an explicit ``except PermissionError`` branch
        between the KeyError (404) branch and the ValueError (422) branch.

        Cross-gateway facade rejections + any future tenant-isolation
        raises now surface as 403 regardless of whether the route caught
        them locally or let them propagate to the middleware fallback.
        """
        request = _make_request()
        call_next = AsyncMock(side_effect=PermissionError("denied"))
        result = await error_handler_middleware(request, call_next)
        assert result.status_code == 403
        body = result.body.decode()
        assert "forbidden" in body
        assert "denied" in body


def _anon_container():
    """Minimal container yielding an anonymous identity (no credentials)."""
    c = SimpleNamespace()
    c.api_key_store = None
    c.actor_registry = None
    c.gateway_id = ""
    c.check_bootstrap_mode = AsyncMock(return_value=False)
    c.config = None
    return c


def _make_app(container, *, auth_enabled: bool = False):
    """Build a real app wiring AuthMiddleware so ``request.app`` exists.

    A bare Starlette request no longer works: the Phase 11 AuthMiddleware reads
    ``request.app.state.container`` to resolve identity, so the middleware must
    run inside an ASGI app that carries the container on ``app.state``.
    """
    app = FastAPI()
    config = SimpleNamespace(enabled=True) if auth_enabled else None
    app.add_middleware(AuthMiddleware, config=config)
    app.state.container = container

    @app.get("/health/ping")
    async def health_ping(request: Request):
        ident = getattr(request.state, "identity", None)
        return {"method": ident.method.value if ident else None}

    @app.get("/health/created")
    async def health_created():
        return JSONResponse(content={"ok": True}, status_code=201)

    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, headers=headers or {})


class TestAuthMiddleware:
    async def test_passes_request_through(self):
        """Phase 11: the unified AuthMiddleware is no longer a no-op stub — it
        resolves a caller identity and stamps it on ``request.state``. With
        dashboard auth DISABLED (the default, backward-compatible mode) it never
        blocks, so an uncredentialed request still passes through exactly as
        before. It is stamped with an anonymous identity rather than being
        ignored entirely (pre-Phase-11 pass-through behavior preserved).
        """
        app = _make_app(_anon_container())
        resp = await _get(app, "/health/ping")
        assert resp.status_code == 200
        assert resp.json()["method"] == "anonymous"

    async def test_preserves_response(self):
        """The middleware returns the downstream response unchanged, including a
        non-200 status code — it only stamps identity, it does not rewrite the
        response when auth is disabled.
        """
        app = _make_app(_anon_container())
        resp = await _get(app, "/health/created")
        assert resp.status_code == 201
        assert resp.json() == {"ok": True}
