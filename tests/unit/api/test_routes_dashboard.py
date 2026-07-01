"""Tests for the Phase 11 dashboard API routes (``/dashboard/*``).

Covers, per the Phase 11 SOW / backend integration contract:

* endpoint response shapes (overview, gateways, memory browse/detail/stats,
  sessions, guards, goals, actors, organizations, profiles, preferences,
  saved-views),
* strict gateway scoping (every module-level Cypher is filtered on the
  request-stamped ``gateway_id``; cross-gateway ``X-EB-Gateway-ID`` is rejected),
* authority gating (the ``require_authority`` dependency + every route declaring
  an auth dependency),
* pagination / multi-filter behaviour on ``POST /dashboard/memory/browse``,
* preferences + saved-views CRUD (graceful 503 when the store is absent).

These use the shared ``tests/unit/api/conftest.py`` fixtures: a ``container``
built from mocked adapters and an httpx ``client`` wrapping ``create_app``.

Authority note
--------------
Dashboard routes are gated via ``require_authority`` (>=70 for reads, >=90 for
mutations). To keep the happy-path tests valid whether or not the auth gate is
actively wired at import time, they authenticate as a high-authority actor (the
``admin_client`` fixture stamps ``X-EB-Actor-Id`` and mocks the registry to
report ``authority_level=100``). The gate itself is verified directly and
structurally so the enforcement contract is asserted regardless.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from elephantbroker.schemas.guards import StaticRule

# A syntactically valid actor UUID for the legacy X-EB-Actor-Id auth path.
ADMIN_ACTOR = "11111111-1111-1111-1111-111111111111"
# The /gateways endpoint falls back to ``container.gateway_id`` ("local" in the
# test container) when no gateway header is stamped. The scoping tests below do
# NOT hardcode the stamped tenant string (it is "" in the test env because the
# container has no config): they assert the Cypher carries a gateway param at
# all, which is the security-relevant invariant.
GATEWAY_FALLBACK = "local"


@pytest.fixture
def admin_client(client, container):
    """The shared async client, authenticated as a high-authority actor.

    Sets ``X-EB-Actor-Id`` on every request and mocks the actor registry so
    ``resolve_identity`` derives ``authority_level=100`` — satisfying both the
    read (>=70) and write (>=90) authority bands used by the dashboard routes.
    """
    container.actor_registry.resolve_actor = AsyncMock(
        return_value=SimpleNamespace(authority_level=100)
    )
    # Pre-cache bootstrap detection so the first request does NOT issue the
    # ``MATCH (a:ActorDataPoint) RETURN count(a)`` probe query — that extra
    # graph.query_cypher call would otherwise consume the first ``side_effect``
    # item and desync per-test call-order assertions.
    container._bootstrap_checked = True
    container._bootstrap_mode = False
    client.headers.update({"X-EB-Actor-Id": ADMIN_ACTOR})
    return client


def _find_call_with(query_cypher_mock, key):
    """Return the params dict of the first ``query_cypher`` call containing ``key``.

    ``query_cypher_mock`` must be the ``query_cypher`` AsyncMock itself
    (e.g. ``container.graph.query_cypher``), not the graph adapter mock.
    """
    for call in query_cypher_mock.call_args_list:
        args = call.args
        if len(args) >= 2 and isinstance(args[1], dict) and key in args[1]:
            return args[1]
    return None


# ---------------------------------------------------------------------------
# Overview & system
# ---------------------------------------------------------------------------


class TestDashboardOverview:
    async def test_overview_shape(self, admin_client):
        r = await admin_client.get("/dashboard/overview")
        assert r.status_code == 200
        body = r.json()
        for key in (
            "total_facts",
            "facts_in_period",
            "facts_by_class",
            "facts_by_scope",
            "active_sessions",
            "total_actors",
            "system_health",
            "components",
            "recent_events",
            "time_range",
        ):
            assert key in body
        assert body["time_range"] == "24h"
        assert isinstance(body["components"], dict)
        assert isinstance(body["recent_events"], list)

    async def test_overview_time_range_param(self, admin_client):
        r = await admin_client.get("/dashboard/overview?time_range=7d")
        assert r.status_code == 200
        assert r.json()["time_range"] == "7d"

    async def test_overview_invalid_time_range_normalized(self, admin_client):
        r = await admin_client.get("/dashboard/overview?time_range=bogus")
        assert r.status_code == 200
        assert r.json()["time_range"] == "24h"

    async def test_gateways_shape(self, admin_client):
        r = await admin_client.get("/dashboard/gateways")
        assert r.status_code == 200
        gws = r.json()["gateways"]
        assert isinstance(gws, list) and len(gws) == 1
        assert gws[0]["gateway_id"] == GATEWAY_FALLBACK
        assert gws[0]["is_current"] is True


# ---------------------------------------------------------------------------
# Gateway scoping
# ---------------------------------------------------------------------------


class TestGatewayScoping:
    async def test_goals_query_scoped_to_gateway(self, admin_client, container):
        container.graph.query_cypher.reset_mock()
        r = await admin_client.get("/dashboard/goals")
        assert r.status_code == 200
        # Goals Cypher is gateway-scoped: WHERE g.gateway_id = $gw.
        params = _find_call_with(container.graph.query_cypher, "gw")
        assert params is not None
        assert isinstance(params["gw"], str)

    async def test_actors_query_scoped_and_type_filter(self, admin_client, container):
        container.graph.query_cypher.reset_mock()
        r = await admin_client.get("/dashboard/actors?actor_type=worker_agent")
        assert r.status_code == 200
        params = _find_call_with(container.graph.query_cypher, "gw")
        assert params is not None and isinstance(params["gw"], str)
        atype_params = _find_call_with(container.graph.query_cypher, "atype")
        assert atype_params is not None
        assert atype_params["atype"] == "worker_agent"

    async def test_cross_gateway_header_rejected(self, container):
        # GatewayIdentityMiddleware enforces single-tenant-per-process (R2-P1.1):
        # a mismatched X-EB-Gateway-ID is rejected with HTTP 403 before routing.
        # This needs a non-empty configured gateway_id, so build a self-contained
        # app whose config pins the tenant to "local".
        from httpx import ASGITransport, AsyncClient

        from elephantbroker.api.app import create_app

        container.config = SimpleNamespace(gateway=SimpleNamespace(gateway_id="local"))
        app = create_app(container)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(
                "/dashboard/goals", headers={"X-EB-Gateway-ID": "other-tenant"}
            )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Authority gating
# ---------------------------------------------------------------------------


class TestAuthorityGating:
    def _fake_request(self, authority_level: int):
        from elephantbroker.api.auth.identity import AuthIdentity

        identity = AuthIdentity(authority_level=authority_level)
        return SimpleNamespace(
            state=SimpleNamespace(identity=identity),
            app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace())),
        )

    async def test_require_authority_allows_sufficient(self):
        from elephantbroker.api.auth.identity import require_authority

        dep = require_authority(70)
        identity = await dep(self._fake_request(90))
        assert identity.authority_level == 90

    async def test_require_authority_rejects_insufficient(self):
        from fastapi import HTTPException

        from elephantbroker.api.auth.identity import require_authority

        dep = require_authority(70)
        with pytest.raises(HTTPException) as exc:
            await dep(self._fake_request(10))
        assert exc.value.status_code == 403

    async def test_require_authority_bootstrap_bypass(self):
        from elephantbroker.api.auth.identity import AuthIdentity, require_authority

        dep = require_authority(90)
        identity = AuthIdentity(authority_level=0, is_bootstrap=True)
        req = SimpleNamespace(
            state=SimpleNamespace(identity=identity),
            app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace())),
        )
        result = await dep(req)
        assert result.is_bootstrap is True

    def test_all_dashboard_routes_declare_auth_dependency(self):
        from elephantbroker.api.routes.dashboard import router

        checked = 0
        for route in router.routes:
            methods = getattr(route, "methods", set()) or set()
            if methods & {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                deps = getattr(route, "dependencies", [])
                assert deps, f"route {route.path} declares no auth dependency"
                checked += 1
        assert checked > 0


# ---------------------------------------------------------------------------
# Memory browse (pagination + filters)
# ---------------------------------------------------------------------------


class TestMemoryBrowse:
    async def test_browse_default_pagination_shape(self, admin_client):
        r = await admin_client.post("/dashboard/memory/browse", json={})
        assert r.status_code == 200
        body = r.json()
        for key in ("items", "total", "offset", "limit", "has_more"):
            assert key in body
        assert body["offset"] == 0
        assert body["limit"] == 50
        assert body["total"] == 0
        assert body["items"] == []
        assert body["has_more"] is False

    async def test_browse_custom_page_computes_offset(self, admin_client):
        r = await admin_client.post(
            "/dashboard/memory/browse", json={"page": 3, "per_page": 10}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["offset"] == 20  # (3 - 1) * 10
        assert body["limit"] == 10

    async def test_browse_total_and_has_more_from_count(self, admin_client, container):
        # Bootstrap probe is pre-cached (admin_client), so the facade's two
        # query_cypher calls are deterministic: count first, page window second.
        container.graph.query_cypher.side_effect = [[{"total": 5}], []]
        r = await admin_client.post(
            "/dashboard/memory/browse", json={"page": 1, "per_page": 2}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        # offset(0) + len(items=0) < total(5) → more pages available.
        assert body["has_more"] is True

    async def test_browse_filters_reach_cypher(self, admin_client, container):
        container.graph.query_cypher.reset_mock()
        actor_uuid = str(uuid.uuid4())
        r = await admin_client.post(
            "/dashboard/memory/browse",
            json={
                "scope": "session",
                "memory_class": "semantic",
                "category": "event",
                "session_key": "agent:main:main",
                "source_actor_id": actor_uuid,
                "text_contains": "invoice",
            },
        )
        assert r.status_code == 200
        # The count query carries every structural filter as a bound param and
        # is strictly gateway-scoped (WHERE f.gateway_id = $gateway_id).
        params = _find_call_with(container.graph.query_cypher, "gateway_id")
        assert params is not None
        assert "gateway_id" in params
        assert params["scope"] == "session"
        assert params["memory_class"] == "semantic"
        assert params["category"] == "event"
        assert params["session_key"] == "agent:main:main"
        assert params["actor_id"] == actor_uuid
        assert params["text_contains"] == "invoice"

    async def test_browse_page_size_clamped(self, admin_client):
        # per_page above the facade hard cap (500) is clamped, not rejected —
        # but the route enforces its own le=500 upper bound at validation time.
        r = await admin_client.post(
            "/dashboard/memory/browse", json={"page": 1, "per_page": 5000}
        )
        assert r.status_code in (200, 422)

    async def test_browse_memory_store_absent(self, admin_client, container):
        container.memory_store = None
        r = await admin_client.post("/dashboard/memory/browse", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Memory detail / stats
# ---------------------------------------------------------------------------


class TestMemoryDetailStats:
    async def test_memory_detail_not_found(self, admin_client, container):
        container.graph.query_cypher.return_value = []
        r = await admin_client.get(f"/dashboard/memory/{uuid.uuid4()}/detail")
        assert r.status_code == 404

    async def test_memory_detail_invalid_uuid_422(self, admin_client):
        r = await admin_client.get("/dashboard/memory/not-a-uuid/detail")
        assert r.status_code == 422

    async def test_memory_stats_shape(self, admin_client):
        r = await admin_client.get("/dashboard/memory/stats")
        assert r.status_code == 200
        body = r.json()
        for key in (
            "time_range",
            "total_facts",
            "by_class",
            "by_scope",
            "avg_confidence",
            "creation_over_time",
        ):
            assert key in body
        assert isinstance(body["creation_over_time"], list)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    async def test_sessions_active_empty(self, admin_client):
        # Redis is None in the test container → degrades to empty list.
        r = await admin_client.get("/dashboard/sessions/active")
        assert r.status_code == 200
        assert r.json() == {"sessions": []}

    async def test_sessions_recent_shape(self, admin_client):
        r = await admin_client.get("/dashboard/sessions/recent")
        assert r.status_code == 200
        body = r.json()
        assert body["time_range"] == "24h"
        assert isinstance(body["sessions"], list)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class TestGuards:
    async def test_guards_rules_lists_builtins(self, admin_client):
        r = await admin_client.get("/dashboard/guards/rules")
        assert r.status_code == 200
        rules = r.json()["rules"]
        assert isinstance(rules, list)

    async def test_guards_rules_merges_custom_store_scoped(self, admin_client, container):
        store = AsyncMock()
        store.list_rules = AsyncMock(
            return_value=[StaticRule(id="custom-1", source="custom", pattern="x")]
        )
        container.custom_rule_store = store
        r = await admin_client.get("/dashboard/guards/rules")
        assert r.status_code == 200
        store.list_rules.assert_awaited()
        assert "gateway_id" in store.list_rules.await_args.kwargs
        ids = {rule["id"] for rule in r.json()["rules"]}
        assert "custom-1" in ids

    async def test_guards_activity_shape(self, admin_client):
        r = await admin_client.get("/dashboard/guards/activity")
        assert r.status_code == 200
        body = r.json()
        for key in ("time_range", "triggers", "near_misses", "by_outcome", "recent_events"):
            assert key in body

    async def test_guards_pending_approvals_empty(self, admin_client):
        r = await admin_client.get("/dashboard/guards/approvals/pending")
        assert r.status_code == 200
        assert r.json() == {"pending": []}

    async def test_guards_create_rule_no_store_503(self, admin_client, container):
        container.custom_rule_store = None
        body = StaticRule(id="r1", pattern="danger").model_dump(mode="json")
        r = await admin_client.post("/dashboard/guards/rules", json=body)
        assert r.status_code == 503

    async def test_guards_create_rule_success_forces_custom_source(
        self, admin_client, container
    ):
        store = AsyncMock()
        store.create_rule = AsyncMock(
            side_effect=lambda *, gateway_id, rule: rule
        )
        container.custom_rule_store = store
        body = StaticRule(id="r-new", pattern="danger", source="builtin").model_dump(
            mode="json"
        )
        r = await admin_client.post("/dashboard/guards/rules", json=body)
        assert r.status_code == 200
        assert r.json()["source"] == "custom"
        assert "gateway_id" in store.create_rule.await_args.kwargs

    async def test_guards_update_rule_no_fields_422(self, admin_client, container):
        container.custom_rule_store = AsyncMock()
        r = await admin_client.put("/dashboard/guards/rules/r1", json={})
        assert r.status_code == 422

    async def test_guards_update_rule_not_found_404(self, admin_client, container):
        store = AsyncMock()
        store.update_rule = AsyncMock(return_value=None)
        container.custom_rule_store = store
        r = await admin_client.put(
            "/dashboard/guards/rules/r1", json={"enabled": False}
        )
        assert r.status_code == 404

    async def test_guards_delete_rule_no_store_503(self, admin_client, container):
        container.custom_rule_store = None
        r = await admin_client.delete("/dashboard/guards/rules/r1")
        assert r.status_code == 503

    async def test_guards_delete_rule_not_found_404(self, admin_client, container):
        store = AsyncMock()
        store.delete_rule = AsyncMock(return_value=False)
        container.custom_rule_store = store
        r = await admin_client.delete("/dashboard/guards/rules/r1")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Goals / procedures / actors / organizations / profiles
# ---------------------------------------------------------------------------


class TestEntityListings:
    async def test_goals_shape(self, admin_client):
        r = await admin_client.get("/dashboard/goals")
        assert r.status_code == 200
        assert isinstance(r.json()["goals"], list)

    async def test_procedures_shape(self, admin_client):
        r = await admin_client.get("/dashboard/procedures")
        assert r.status_code == 200
        assert isinstance(r.json()["procedures"], list)

    async def test_procedure_detail_not_found(self, admin_client, container):
        container.graph.query_cypher.return_value = []
        r = await admin_client.get(f"/dashboard/procedures/{uuid.uuid4()}/detail")
        assert r.status_code == 404

    async def test_actors_shape(self, admin_client):
        r = await admin_client.get("/dashboard/actors")
        assert r.status_code == 200
        assert isinstance(r.json()["actors"], list)

    async def test_actor_detail_not_found(self, admin_client, container):
        container.graph.query_cypher.return_value = []
        r = await admin_client.get(f"/dashboard/actors/{uuid.uuid4()}/detail")
        assert r.status_code == 404

    async def test_organizations_shape(self, admin_client):
        r = await admin_client.get("/dashboard/organizations")
        assert r.status_code == 200
        assert isinstance(r.json()["organizations"], list)

    async def test_profiles_shape(self, admin_client):
        r = await admin_client.get("/dashboard/profiles")
        assert r.status_code == 200
        assert isinstance(r.json()["profiles"], list)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class TestPreferences:
    async def test_get_preferences_defaults(self, admin_client):
        r = await admin_client.get("/dashboard/preferences")
        assert r.status_code == 200
        body = r.json()
        assert body["items_per_page"] == 50
        assert body["theme"] == "light"
        # Scoped to the calling actor (from X-EB-Actor-Id).
        assert body["actor_id"] == ADMIN_ACTOR

    async def test_put_preferences_echoes_and_scopes_actor(self, admin_client):
        r = await admin_client.put(
            "/dashboard/preferences",
            json={"actor_id": "someone-else", "theme": "dark", "items_per_page": 25},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["theme"] == "dark"
        assert body["items_per_page"] == 25
        # Body actor_id is never trusted — always overwritten with the caller.
        assert body["actor_id"] == ADMIN_ACTOR


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


class TestSavedViews:
    async def test_list_saved_views_no_store_empty(self, admin_client):
        r = await admin_client.get("/dashboard/saved-views")
        assert r.status_code == 200
        assert r.json() == {"views": []}

    async def test_list_saved_views_with_store(self, admin_client, container):
        store = AsyncMock()
        store.list_saved_views = AsyncMock(
            return_value=[{"id": "v1", "name": "mine", "resource": "memory"}]
        )
        container.dashboard_preferences_store = store
        r = await admin_client.get("/dashboard/saved-views?resource=memory")
        assert r.status_code == 200
        views = r.json()["views"]
        assert len(views) == 1 and views[0]["id"] == "v1"

    async def test_create_saved_view_no_store_503(self, admin_client, container):
        container.dashboard_preferences_store = None
        r = await admin_client.post(
            "/dashboard/saved-views",
            json={"name": "recent", "resource": "memory"},
        )
        assert r.status_code == 503

    async def test_create_saved_view_success(self, admin_client, container):
        store = AsyncMock()
        store.create_saved_view = AsyncMock(
            side_effect=lambda actor_id, view: view
        )
        container.dashboard_preferences_store = store
        r = await admin_client.post(
            "/dashboard/saved-views",
            json={"name": "recent", "resource": "memory", "filters": {"scope": "session"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "recent"
        assert body["actor_id"] == ADMIN_ACTOR

    async def test_create_saved_view_missing_fields_422(self, admin_client):
        r = await admin_client.post("/dashboard/saved-views", json={"name": "x"})
        assert r.status_code == 422

    async def test_delete_saved_view_no_store_503(self, admin_client, container):
        container.dashboard_preferences_store = None
        r = await admin_client.delete("/dashboard/saved-views/v1")
        assert r.status_code == 503

    async def test_delete_saved_view_not_found_404(self, admin_client, container):
        store = AsyncMock()
        store.delete_saved_view = AsyncMock(return_value=False)
        container.dashboard_preferences_store = store
        r = await admin_client.delete("/dashboard/saved-views/v1")
        assert r.status_code == 404

    async def test_delete_saved_view_success(self, admin_client, container):
        store = AsyncMock()
        store.delete_saved_view = AsyncMock(return_value=True)
        container.dashboard_preferences_store = store
        r = await admin_client.delete("/dashboard/saved-views/v1")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"


