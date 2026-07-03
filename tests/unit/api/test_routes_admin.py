"""Tests for admin API routes.

Covers: bootstrap-status, create org, list orgs, create team, create goal,
and authority enforcement on admin endpoints.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from elephantbroker.schemas.actor import ActorRef, ActorType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADMIN_ACTOR_ID = str(uuid.uuid4())


def _admin_headers() -> dict[str, str]:
    """Headers that simulate an authenticated admin actor."""
    return {"X-EB-Actor-Id": _ADMIN_ACTOR_ID}


def _make_admin_actor(authority_level: int = 90) -> ActorRef:
    return ActorRef(
        id=uuid.UUID(_ADMIN_ACTOR_ID),
        type=ActorType.HUMAN_COORDINATOR,
        display_name="admin",
        authority_level=authority_level,
    )


def _enable_bootstrap(container):
    """Put the container in bootstrap mode (cached, no graph query)."""
    container._bootstrap_mode = True
    container._bootstrap_checked = True


def _disable_bootstrap(container):
    """Take the container out of bootstrap mode (cached, no graph query)."""
    container._bootstrap_mode = False
    container._bootstrap_checked = True


# ---------------------------------------------------------------------------
# Bootstrap status
# ---------------------------------------------------------------------------

class TestBootstrapStatus:
    async def test_bootstrap_status_returns_mode(self, client, container):
        _enable_bootstrap(container)
        r = await client.get("/admin/bootstrap-status")
        assert r.status_code == 200
        assert r.json()["bootstrap_mode"] is True

    async def test_bootstrap_status_false_by_default(self, client, container):
        _disable_bootstrap(container)
        r = await client.get("/admin/bootstrap-status")
        assert r.status_code == 200
        assert r.json()["bootstrap_mode"] is False


# ---------------------------------------------------------------------------
# Organizations — requires authority
# ---------------------------------------------------------------------------

class TestCreateOrganization:
    async def test_create_org_in_bootstrap_mode(self, client, container):
        """In bootstrap mode, org creation succeeds without a real actor."""
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/organizations",
            json={"name": "Acme Corp"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Acme Corp"
        assert "org_id" in data

    async def test_create_org_missing_name_422(self, client, container):
        """Empty name triggers Pydantic validation error."""
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/organizations",
            json={"name": ""},
            headers=_admin_headers(),
        )
        assert r.status_code == 422

    async def test_create_org_without_actor_id_header_401(self, client, container):
        """Missing X-EB-Actor-Id header in non-bootstrap mode gives 401."""
        _disable_bootstrap(container)
        r = await client.post(
            "/admin/organizations",
            json={"name": "No Auth Org"},
        )
        assert r.status_code == 401


class TestListOrganizations:
    async def test_list_orgs_in_bootstrap_mode(self, client, container, mock_graph):
        """List orgs returns empty list from empty graph."""
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = []
        r = await client.get(
            "/admin/organizations",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json() == []

    async def test_list_orgs_returns_records(self, client, container, mock_graph):
        """List orgs surfaces records from graph query."""
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = [
            {"props": {"eb_id": "o1", "name": "Org1", "display_label": "O1"}},
            {"props": {"eb_id": "o2", "name": "Org2", "display_label": "O2"}},
        ]
        r = await client.get(
            "/admin/organizations",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert data[0]["name"] == "Org1"


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

class TestCreateTeam:
    async def test_create_team_in_bootstrap_mode(self, client, container):
        """Team creation works in bootstrap mode."""
        _enable_bootstrap(container)
        org_id = str(uuid.uuid4())
        r = await client.post(
            "/admin/teams",
            json={"name": "Engineering", "org_id": org_id},
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Engineering"
        assert data["org_id"] == org_id
        assert "team_id" in data

    async def test_create_team_missing_org_id_422(self, client, container):
        """Missing org_id triggers validation error."""
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/teams",
            json={"name": "NoOrg"},
            headers=_admin_headers(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

class TestCreateGoal:
    async def test_create_goal_actor_scope(self, client, container):
        """Create a goal with actor scope -- requires resolved actor with authority."""
        _disable_bootstrap(container)
        admin = _make_admin_actor(authority_level=90)
        container.actor_registry.resolve_actor = AsyncMock(return_value=admin)
        # create_actor_goal requires min_authority_level=0 by default
        container.authority_store.get_rule = AsyncMock(
            return_value={"min_authority_level": 0, "require_self_ownership": True},
        )
        r = await client.post(
            "/admin/goals",
            json={"title": "Ship v1", "scope": "actor"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Ship v1"
        assert data["scope"] == "actor"

    async def test_create_goal_missing_title_422(self, client, container):
        """Empty title triggers validation error."""
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/goals",
            json={"title": ""},
            headers=_admin_headers(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Authority enforcement
# ---------------------------------------------------------------------------

class TestAuthorityEnforcement:
    async def test_low_authority_actor_denied_org_creation(self, client, container):
        """An actor with authority_level=30 cannot create orgs (requires 90)."""
        _disable_bootstrap(container)
        low_actor = _make_admin_actor(authority_level=30)
        container.actor_registry.resolve_actor = AsyncMock(return_value=low_actor)
        container.authority_store.get_rule = AsyncMock(
            return_value={"min_authority_level": 90},
        )

        r = await client.post(
            "/admin/organizations",
            json={"name": "Unauthorized Org"},
            headers=_admin_headers(),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Actor listing — GET /admin/actors + GET /admin/teams/{id}/members
# (soft-deactivated actors hidden by default, include_inactive opt-in)
# ---------------------------------------------------------------------------

_ACTIVE_CLAUSE = "(a.active = true OR a.active IS NULL)"


class TestListActorsActiveFilter:
    async def test_list_actors_hides_inactive_by_default(
        self, client, container, mock_graph
    ):
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = []
        r = await client.get("/admin/actors", headers=_admin_headers())
        assert r.status_code == 200
        cypher = mock_graph.query_cypher.call_args[0][0]
        assert _ACTIVE_CLAUSE in cypher

    async def test_list_actors_include_inactive_returns_everything(
        self, client, container, mock_graph
    ):
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = [
            {"props": {"eb_id": "a1", "display_name": "Dead", "actor_type": "worker_agent",
                       "authority_level": 0, "active": False}},
        ]
        r = await client.get(
            "/admin/actors?include_inactive=true", headers=_admin_headers()
        )
        assert r.status_code == 200
        assert len(r.json()) == 1
        cypher = mock_graph.query_cypher.call_args[0][0]
        assert _ACTIVE_CLAUSE not in cypher

    async def test_list_actors_org_filter_keeps_active_default(
        self, client, container, mock_graph
    ):
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = []
        r = await client.get(
            "/admin/actors?org_id=o1", headers=_admin_headers()
        )
        assert r.status_code == 200
        cypher = mock_graph.query_cypher.call_args[0][0]
        assert "a.org_id = $org" in cypher
        assert _ACTIVE_CLAUSE in cypher

    async def test_list_team_members_hides_inactive_by_default(
        self, client, container, mock_graph
    ):
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = []
        r = await client.get(
            f"/admin/teams/{uuid.uuid4()}/members", headers=_admin_headers()
        )
        assert r.status_code == 200
        cypher = mock_graph.query_cypher.call_args[0][0]
        assert _ACTIVE_CLAUSE in cypher

    async def test_list_team_members_include_inactive_returns_everything(
        self, client, container, mock_graph
    ):
        _enable_bootstrap(container)
        mock_graph.query_cypher.return_value = []
        r = await client.get(
            f"/admin/teams/{uuid.uuid4()}/members?include_inactive=true",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        cypher = mock_graph.query_cypher.call_args[0][0]
        assert _ACTIVE_CLAUSE not in cypher


# ---------------------------------------------------------------------------
# Actor handle resolution — GET /admin/actors/resolve (TF-08-007)
# ---------------------------------------------------------------------------

class TestResolveActorByHandle:
    async def test_resolve_handle_found_returns_actor(self, client, container):
        _enable_bootstrap(container)
        target = ActorRef(
            type=ActorType.HUMAN_COORDINATOR,
            display_name="Alice",
            authority_level=70,
            handles=["email:alice@example.com"],
        )
        container.actor_registry.resolve_by_handle = AsyncMock(return_value=target)

        r = await client.get(
            "/admin/actors/resolve",
            params={"handle": "email:alice@example.com"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["display_name"] == "Alice"
        assert "email:alice@example.com" in data["handles"]
        container.actor_registry.resolve_by_handle.assert_called_once_with("email:alice@example.com")

    async def test_resolve_handle_not_found_returns_404(self, client, container):
        _enable_bootstrap(container)
        container.actor_registry.resolve_by_handle = AsyncMock(return_value=None)

        r = await client.get(
            "/admin/actors/resolve",
            params={"handle": "email:ghost@example.com"},
            headers=_admin_headers(),
        )
        assert r.status_code == 404
        assert "ghost@example.com" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Actor registration — display_name validation (TF-08-014)
# ---------------------------------------------------------------------------

class TestRegisterActorValidation:
    async def test_register_actor_empty_display_name_returns_422(self, client, container):
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/actors",
            json={"display_name": "", "type": "worker_agent"},
            headers=_admin_headers(),
        )
        assert r.status_code == 422
        assert "display_name" in r.json()["detail"]

    async def test_register_actor_whitespace_display_name_returns_422(self, client, container):
        _enable_bootstrap(container)
        r = await client.post(
            "/admin/actors",
            json={"display_name": "   ", "type": "worker_agent"},
            headers=_admin_headers(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Team member dual-write regression (TF-08-005)
# ---------------------------------------------------------------------------

class TestTeamMemberDualWrite:
    """Verify that add/remove team member updates both the MEMBER_OF edge AND
    the team_ids node property — the dual-write that lets authority checks
    against ``actor_entity["team_ids"]`` stay consistent with edge state.
    """

    async def test_add_member_dual_writes_team_ids(self, client, container, mock_graph, monkeypatch):
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        team_id = str(uuid.uuid4())

        # get_entity returns a populated actor with no current teams
        mock_graph.get_entity = AsyncMock(return_value={
            "eb_id": actor_id,
            "display_name": "Bob",
            "actor_type": "worker_agent",
            "authority_level": 0,
            "handles": [],
            "org_id": None,
            "team_ids": [],
            "trust_level": 0.5,
            "tags": [],
            "gateway_id": "test",
        })

        recorded = []

        async def fake_add(data_points, context=None, custom_edges=None, embed_triplets=False):
            recorded.extend(list(data_points))
            return list(data_points)

        monkeypatch.setattr(
            "elephantbroker.api.routes.admin.add_data_points", fake_add,
        )

        r = await client.post(
            f"/admin/teams/{team_id}/members",
            json={"actor_id": actor_id},
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        # Edge mutation
        mock_graph.add_relation.assert_called_once()
        # Property mutation: team_id appended to team_ids
        assert len(recorded) == 1
        dp = recorded[0]
        assert team_id in dp.team_ids
        assert dp.eb_id == actor_id

    async def test_remove_member_dual_writes_team_ids(self, client, container, mock_graph, monkeypatch):
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        team_id = str(uuid.uuid4())
        other_team = str(uuid.uuid4())

        mock_graph.get_entity = AsyncMock(return_value={
            "eb_id": actor_id,
            "display_name": "Bob",
            "actor_type": "worker_agent",
            "authority_level": 0,
            "handles": [],
            "org_id": None,
            "team_ids": [team_id, other_team],
            "trust_level": 0.5,
            "tags": [],
            "gateway_id": "test",
        })
        # delete_relation exists on the mock
        mock_graph.delete_relation = AsyncMock()

        recorded = []

        async def fake_add(data_points, context=None, custom_edges=None, embed_triplets=False):
            recorded.extend(list(data_points))
            return list(data_points)

        monkeypatch.setattr(
            "elephantbroker.api.routes.admin.add_data_points", fake_add,
        )

        r = await client.delete(
            f"/admin/teams/{team_id}/members/{actor_id}",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert len(recorded) == 1
        dp = recorded[0]
        assert team_id not in dp.team_ids
        assert other_team in dp.team_ids


# ---------------------------------------------------------------------------
# Fact indexes (Fix 5) — /admin/indexes: opt-in, per-index, authority-gated
# ---------------------------------------------------------------------------

def _grant_manage_indexes(container, authority_level: int = 90):
    """Authorize the test admin for the manage_indexes action (level 90)."""
    _disable_bootstrap(container)
    admin = _make_admin_actor(authority_level=authority_level)
    container.actor_registry.resolve_actor = AsyncMock(return_value=admin)
    container.authority_store.get_rule = AsyncMock(
        return_value={"min_authority_level": 90},
    )


class TestFactIndexAuthority:
    def test_manage_indexes_default_rule_is_config_class(self):
        """Indexes are database-global config — level 90, same as create_org."""
        from elephantbroker.runtime.profiles.authority_store import AUTHORITY_DEFAULTS
        assert AUTHORITY_DEFAULTS["manage_indexes"] == {"min_authority_level": 90}

    def test_manage_indexes_not_a_bootstrap_action(self):
        """Bootstrap mode must NOT bypass the index-management gate."""
        from elephantbroker.api.routes._authority import BOOTSTRAP_ACTIONS
        assert "manage_indexes" not in BOOTSTRAP_ACTIONS

    async def test_list_indexes_without_actor_401(self, client, container):
        _disable_bootstrap(container)
        r = await client.get("/admin/indexes")
        assert r.status_code == 401

    async def test_create_index_without_actor_401(self, client, container):
        _disable_bootstrap(container)
        r = await client.post("/admin/indexes/eb_fact_gateway_id")
        assert r.status_code == 401

    async def test_low_authority_actor_denied_403(self, client, container):
        _disable_bootstrap(container)
        low_actor = _make_admin_actor(authority_level=70)
        container.actor_registry.resolve_actor = AsyncMock(return_value=low_actor)
        container.authority_store.get_rule = AsyncMock(
            return_value={"min_authority_level": 90},
        )
        for method, path in (
            ("GET", "/admin/indexes"),
            ("POST", "/admin/indexes/eb_fact_gateway_id"),
            ("DELETE", "/admin/indexes/eb_fact_gateway_id"),
            ("POST", "/admin/indexes/eb_fact_gateway_id/rebuild"),
        ):
            r = await client.request(method, path, headers=_admin_headers())
            assert r.status_code == 403, f"{method} {path} should 403"


class TestFactIndexRoutes:
    async def test_list_indexes_returns_catalog_with_live_status(
        self, client, container, mock_graph,
    ):
        _grant_manage_indexes(container)
        mock_graph.query_cypher.return_value = [
            {"name": "eb_fact_gateway_id", "state": "ONLINE", "populationPercent": 100.0},
        ]
        r = await client.get("/admin/indexes", headers=_admin_headers())
        assert r.status_code == 200
        indexes = r.json()["indexes"]
        assert len(indexes) == 5  # full catalog, existing or not
        by_name = {i["name"]: i for i in indexes}
        assert by_name["eb_fact_gateway_id"]["exists"] is True
        assert by_name["eb_fact_gateway_id"]["state"] == "ONLINE"
        assert by_name["eb_fact_gateway_id"]["population_percent"] == 100.0
        assert by_name["eb_fact_created_at"]["exists"] is False
        assert by_name["eb_fact_created_at"]["state"] is None

    async def test_create_index_happy_path(self, client, container, mock_graph):
        _grant_manage_indexes(container)
        r = await client.post(
            "/admin/indexes/eb_fact_created_at", headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json() == {"index": "eb_fact_created_at", "status": "created"}
        mock_graph.query_cypher.assert_awaited_once_with(
            "CREATE INDEX eb_fact_created_at IF NOT EXISTS "
            "FOR (f:FactDataPoint) ON (f.created_at)"
        )

    async def test_drop_index_happy_path(self, client, container, mock_graph):
        _grant_manage_indexes(container)
        r = await client.delete(
            "/admin/indexes/eb_fact_scope", headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json() == {"index": "eb_fact_scope", "status": "dropped"}
        mock_graph.query_cypher.assert_awaited_once_with(
            "DROP INDEX eb_fact_scope IF EXISTS"
        )

    async def test_rebuild_index_drops_then_creates(self, client, container, mock_graph):
        _grant_manage_indexes(container)
        r = await client.post(
            "/admin/indexes/eb_fact_confidence/rebuild", headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json() == {"index": "eb_fact_confidence", "status": "rebuilt"}
        issued = [c.args[0] for c in mock_graph.query_cypher.await_args_list]
        assert issued == [
            "DROP INDEX eb_fact_confidence IF EXISTS",
            "CREATE INDEX eb_fact_confidence IF NOT EXISTS "
            "FOR (f:FactDataPoint) ON (f.confidence)",
        ]

    async def test_unknown_index_name_404(self, client, container, mock_graph):
        _grant_manage_indexes(container)
        for method, path in (
            ("POST", "/admin/indexes/eb_fact_bogus"),
            ("DELETE", "/admin/indexes/eb_fact_bogus"),
            ("POST", "/admin/indexes/eb_fact_bogus/rebuild"),
        ):
            r = await client.request(method, path, headers=_admin_headers())
            assert r.status_code == 404, f"{method} {path} should 404"
            assert "Unknown fact index" in r.json()["detail"]
        mock_graph.query_cypher.assert_not_awaited()  # whitelist blocks before DDL

    async def test_memory_store_unavailable_503(self, client, container):
        """Context-only deployments have no memory facade — clean 503."""
        _grant_manage_indexes(container)
        container.memory_store = None
        r = await client.get("/admin/indexes", headers=_admin_headers())
        assert r.status_code == 503


class TestFactIndexesOptInInvariant:
    def test_container_and_lifespan_never_create_indexes(self):
        """FEATURE INVARIANT: indexes are opt-in, DEFAULT OFF. The DI
        container and the FastAPI app/lifespan must not reference the
        fact-index creation helpers — creation happens ONLY through the
        explicit /admin/indexes surface."""
        import inspect

        from elephantbroker.api import app as app_module
        from elephantbroker.runtime import container as container_module

        for mod in (container_module, app_module):
            src = inspect.getsource(mod)
            assert "ensure_fact_index" not in src, (
                f"{mod.__name__} must not create fact indexes at startup"
            )
