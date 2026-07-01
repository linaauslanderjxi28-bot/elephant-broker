"""Unit tests for the unified :class:`AuthMiddleware` (Phase 11).

Covers the three credential paths (API key, gateway identity, actor header),
anonymous stamping, route-class enforcement when dashboard auth is enabled
(401 on unauthenticated protected routes, unprotected skip, flexible pass), and
the ``TryRefreshTokenError`` → 401 mapping. SuperTokens/DB are mocked; a fake
``supertokens_python`` exceptions module is injected only for the refresh test.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from elephantbroker.api.auth.api_key_store import ApiKeyRecord
from elephantbroker.api.middleware.auth import AuthMiddleware


def _container(*, api_key_store=None, actor_registry=None, bootstrap: bool = False):
    c = SimpleNamespace()
    c.api_key_store = api_key_store
    c.actor_registry = actor_registry
    c.gateway_id = ""
    c.check_bootstrap_mode = AsyncMock(return_value=bootstrap)
    c.config = None
    return c


def _make_app(container, *, auth_enabled: bool = False):
    app = FastAPI()
    config = SimpleNamespace(enabled=True) if auth_enabled else None
    app.add_middleware(AuthMiddleware, config=config)
    app.state.container = container

    def _ident(request: Request):
        ident = getattr(request.state, "identity", None)
        return {
            "method": ident.method.value if ident else None,
            "authority_level": ident.authority_level if ident else None,
            "actor_id": ident.actor_id if ident else None,
        }

    @app.get("/dashboard/ping")
    async def dashboard_ping(request: Request):
        return _ident(request)

    @app.get("/health/ping")
    async def health_ping(request: Request):
        return _ident(request)

    @app.get("/trace/ping")
    async def trace_ping(request: Request):
        return _ident(request)

    @app.get("/admin/ping")
    async def admin_ping(request: Request):
        return _ident(request)

    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, headers=headers or {})


def _key_store(record):
    store = AsyncMock()
    store.validate = AsyncMock(return_value=record)
    return store


class TestIdentityStamping:
    async def test_anonymous_stamped(self):
        app = _make_app(_container())
        resp = await _get(app, "/dashboard/ping")
        assert resp.status_code == 200
        assert resp.json()["method"] == "anonymous"

    async def test_api_key_path(self):
        record = ApiKeyRecord(
            key_id="k1", gateway_id="", label="l", key_prefix="eb_ak_ab",
            authority_level=70, actor_id="a1",
        )
        app = _make_app(_container(api_key_store=_key_store(record)))
        resp = await _get(app, "/dashboard/ping", {"X-EB-API-Key": "eb_ak_secret"})
        body = resp.json()
        assert body["method"] == "api_key"
        assert body["authority_level"] == 70

    async def test_gateway_identity_path(self):
        app = _make_app(_container())
        resp = await _get(app, "/trace/ping", {"X-EB-Agent-Key": ":main"})
        assert resp.json()["method"] == "gateway_identity"

    async def test_actor_header_path(self):
        import uuid
        from elephantbroker.schemas.actor import ActorRef, ActorType

        actor = ActorRef(
            type=ActorType.HUMAN_COORDINATOR, display_name="op", authority_level=60,
        )
        reg = AsyncMock()
        reg.resolve_actor = AsyncMock(return_value=actor)
        app = _make_app(_container(actor_registry=reg))
        resp = await _get(app, "/admin/ping", {"X-EB-Actor-Id": str(uuid.uuid4())})
        body = resp.json()
        assert body["method"] == "actor_header"
        assert body["authority_level"] == 60


class TestRouteClassEnforcement:
    async def test_disabled_never_blocks(self):
        # Auth disabled (default): protected route reachable anonymously.
        app = _make_app(_container(), auth_enabled=False)
        resp = await _get(app, "/dashboard/ping")
        assert resp.status_code == 200

    async def test_enabled_blocks_unauthenticated_dashboard(self):
        app = _make_app(_container(), auth_enabled=True)
        resp = await _get(app, "/dashboard/ping")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Authentication required"

    async def test_enabled_blocks_unauthenticated_admin(self):
        app = _make_app(_container(), auth_enabled=True)
        resp = await _get(app, "/admin/ping")
        assert resp.status_code == 401

    async def test_enabled_skips_unprotected_health(self):
        app = _make_app(_container(), auth_enabled=True)
        resp = await _get(app, "/health/ping")
        assert resp.status_code == 200

    async def test_enabled_allows_authenticated_dashboard(self):
        record = ApiKeyRecord(
            key_id="k1", gateway_id="", label="l", key_prefix="eb_ak_ab",
            authority_level=70,
        )
        app = _make_app(_container(api_key_store=_key_store(record)), auth_enabled=True)
        resp = await _get(app, "/dashboard/ping", {"X-EB-API-Key": "eb_ak_secret"})
        assert resp.status_code == 200
        assert resp.json()["method"] == "api_key"

    async def test_enabled_flexible_trace_allows_anonymous(self):
        # /trace is FLEXIBLE — not AUTH_REQUIRED, so it is not blocked.
        app = _make_app(_container(), auth_enabled=True)
        resp = await _get(app, "/trace/ping")
        assert resp.status_code == 200

    async def test_enabled_bootstrap_bypasses_block(self):
        app = _make_app(_container(bootstrap=True), auth_enabled=True)
        resp = await _get(app, "/dashboard/ping")
        assert resp.status_code == 200


class TestTokenRefresh:
    async def test_try_refresh_returns_401(self, monkeypatch):
        """A raised TryRefreshTokenError → HTTP 401 {"error": "Token expired"}."""
        # Inject a fake supertokens exceptions module so the middleware's
        # ``from supertokens_python...import TryRefreshTokenError`` succeeds.
        class FakeTryRefresh(Exception):
            pass

        pkg = ModuleType("supertokens_python")
        recipe = ModuleType("supertokens_python.recipe")
        session = ModuleType("supertokens_python.recipe.session")
        exceptions = ModuleType("supertokens_python.recipe.session.exceptions")
        exceptions.TryRefreshTokenError = FakeTryRefresh
        pkg.recipe = recipe
        recipe.session = session
        session.exceptions = exceptions
        monkeypatch.setitem(sys.modules, "supertokens_python", pkg)
        monkeypatch.setitem(sys.modules, "supertokens_python.recipe", recipe)
        monkeypatch.setitem(sys.modules, "supertokens_python.recipe.session", session)
        monkeypatch.setitem(
            sys.modules, "supertokens_python.recipe.session.exceptions", exceptions
        )

        async def _raise(request, container):
            raise FakeTryRefresh()

        monkeypatch.setattr(
            "elephantbroker.api.auth.identity.resolve_identity", _raise
        )

        app = _make_app(_container())
        resp = await _get(app, "/dashboard/ping")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Token expired"
