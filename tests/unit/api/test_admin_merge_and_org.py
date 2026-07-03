"""Unit tests for the two branch-new admin actor routes (EB-FE):

* ``POST /admin/actors/{actor_id}/merge`` — delegates a duplicate→survivor merge
  to ``container.actor_registry.merge_actors(UUID(survivor), UUID(duplicate))``
  and returns the surviving ``ActorRef`` (``model_dump(mode="json")``). Falls
  back to a stable ``501`` when the registry lacks the capability.
* ``PUT /admin/actors/{actor_id}/organization`` — sets or clears an actor's
  ``org_id`` property (organization membership) via
  ``ActorRegistry.register_actor``.

All I/O is mocked (graph adapter, actor registry, ``add_data_points`` — the last
is already patched globally by the ``_mock_cognee_apis`` autouse fixture in
``conftest.py``). Mirrors the mocking style of ``test_routes_admin.py`` in the
same directory: the shared ``client``/``container``/``mock_graph`` fixtures, the
``X-EB-Actor-Id`` admin header, and the bootstrap on/off helpers.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from elephantbroker.schemas.actor import ActorRef, ActorType


# ---------------------------------------------------------------------------
# Helpers (mirrors test_routes_admin.py)
# ---------------------------------------------------------------------------

_ADMIN_ACTOR_ID = str(uuid.uuid4())


def _admin_headers() -> dict[str, str]:
    return {"X-EB-Actor-Id": _ADMIN_ACTOR_ID}


def _make_admin_actor(authority_level: int = 90) -> ActorRef:
    return ActorRef(
        id=uuid.UUID(_ADMIN_ACTOR_ID),
        type=ActorType.HUMAN_COORDINATOR,
        display_name="admin",
        authority_level=authority_level,
    )


def _make_actor(actor_id: str, *, org_id: uuid.UUID | None = None) -> ActorRef:
    return ActorRef(
        id=uuid.UUID(actor_id),
        type=ActorType.WORKER_AGENT,
        display_name="worker",
        authority_level=0,
        org_id=org_id,
    )


def _enable_bootstrap(container):
    container._bootstrap_mode = True
    container._bootstrap_checked = True


def _disable_bootstrap(container):
    container._bootstrap_mode = False
    container._bootstrap_checked = True


def _authorize_admin(container):
    """Make ``_auth`` pass for a merge (``merge_actors`` is NOT a bootstrap
    action, so the caller must resolve to a real actor with sufficient level).

    The conftest ``authority_store.get_rule`` default is
    ``{"min_authority_level": 0}``, so any resolved actor clears the bar.
    """
    _disable_bootstrap(container)
    container.actor_registry.resolve_actor = AsyncMock(return_value=_make_admin_actor())


# ---------------------------------------------------------------------------
# POST /admin/actors/{actor_id}/merge
# ---------------------------------------------------------------------------

class TestMergeActors:
    async def test_merge_delegates_to_registry_and_returns_survivor(
        self, client, container
    ):
        """Happy path: the route delegates to
        ``registry.merge_actors(UUID(survivor), UUID(duplicate))`` and returns
        the surviving ActorRef as JSON."""
        _authorize_admin(container)
        survivor_id = str(uuid.uuid4())
        duplicate_id = str(uuid.uuid4())
        survivor = ActorRef(
            id=uuid.UUID(survivor_id),
            type=ActorType.HUMAN_COORDINATOR,
            display_name="Survivor",
            authority_level=50,
            handles=["email:s@example.com"],
        )
        container.actor_registry.merge_actors = AsyncMock(return_value=survivor)

        r = await client.post(
            f"/admin/actors/{survivor_id}/merge",
            json={"duplicate_id": duplicate_id},
            headers=_admin_headers(),
        )

        assert r.status_code == 200
        data = r.json()
        assert data["id"] == survivor_id
        assert data["display_name"] == "Survivor"
        # Delegation: survivor from the path, duplicate from the body, both as UUIDs.
        container.actor_registry.merge_actors.assert_awaited_once_with(
            uuid.UUID(survivor_id), uuid.UUID(duplicate_id)
        )

    async def test_merge_missing_duplicate_id_returns_400(self, client, container):
        """No ``duplicate_id`` in the body -> 400, registry never touched."""
        _authorize_admin(container)
        container.actor_registry.merge_actors = AsyncMock()
        survivor_id = str(uuid.uuid4())

        r = await client.post(
            f"/admin/actors/{survivor_id}/merge",
            json={},
            headers=_admin_headers(),
        )

        assert r.status_code == 400
        assert "duplicate_id" in r.json()["detail"]
        container.actor_registry.merge_actors.assert_not_called()

    async def test_merge_empty_duplicate_id_returns_400(self, client, container):
        """An empty-string ``duplicate_id`` is falsy -> 400."""
        _authorize_admin(container)
        container.actor_registry.merge_actors = AsyncMock()

        r = await client.post(
            f"/admin/actors/{uuid.uuid4()}/merge",
            json={"duplicate_id": ""},
            headers=_admin_headers(),
        )

        assert r.status_code == 400
        container.actor_registry.merge_actors.assert_not_called()

    async def test_merge_bad_duplicate_uuid_returns_422(self, client, container):
        """A non-UUID ``duplicate_id`` fails ``uuid.UUID()`` (ValueError -> 422
        via the error-handler middleware) before the registry is called."""
        _authorize_admin(container)
        container.actor_registry.merge_actors = AsyncMock()

        r = await client.post(
            f"/admin/actors/{uuid.uuid4()}/merge",
            json={"duplicate_id": "not-a-uuid"},
            headers=_admin_headers(),
        )

        assert r.status_code == 422
        container.actor_registry.merge_actors.assert_not_called()

    async def test_merge_bad_survivor_uuid_returns_422(self, client, container):
        """A non-UUID survivor (path param) fails ``uuid.UUID()`` -> 422."""
        _authorize_admin(container)
        container.actor_registry.merge_actors = AsyncMock()

        r = await client.post(
            "/admin/actors/not-a-uuid/merge",
            json={"duplicate_id": str(uuid.uuid4())},
            headers=_admin_headers(),
        )

        assert r.status_code == 422
        container.actor_registry.merge_actors.assert_not_called()

    async def test_merge_self_merge_delegated_registry_rejects(self, client, container):
        """The route has NO self-merge guard of its own — it delegates and the
        registry raises ``ValueError('Cannot merge an actor into itself')``,
        which the error middleware surfaces as 422. Documents that self-merge
        rejection lives in the registry, not the route."""
        _authorize_admin(container)
        same_id = str(uuid.uuid4())
        container.actor_registry.merge_actors = AsyncMock(
            side_effect=ValueError("Cannot merge an actor into itself")
        )

        r = await client.post(
            f"/admin/actors/{same_id}/merge",
            json={"duplicate_id": same_id},
            headers=_admin_headers(),
        )

        assert r.status_code == 422
        # It still delegated (no route-level short-circuit for self-merge).
        container.actor_registry.merge_actors.assert_awaited_once_with(
            uuid.UUID(same_id), uuid.UUID(same_id)
        )

    async def test_merge_returns_501_when_registry_lacks_capability(
        self, client, container
    ):
        """When the registry does not expose ``merge_actors`` the route returns
        a stable 501 the dashboard uses to disable the action."""
        _disable_bootstrap(container)
        # Replace the registry with one that has resolve_actor (needed for auth)
        # but NOT merge_actors, so ``hasattr(...)`` is False.
        fake_registry = MagicMock(spec=["resolve_actor", "register_actor"])
        fake_registry.resolve_actor = AsyncMock(return_value=_make_admin_actor())
        container.actor_registry = fake_registry

        r = await client.post(
            f"/admin/actors/{uuid.uuid4()}/merge",
            json={"duplicate_id": str(uuid.uuid4())},
            headers=_admin_headers(),
        )

        assert r.status_code == 501
        assert "not supported" in r.json()["detail"].lower()

    async def test_merge_requires_auth_in_non_bootstrap_mode(self, client, container):
        """No caller identity + non-bootstrap -> 401 before any delegation."""
        _disable_bootstrap(container)
        container.actor_registry.merge_actors = AsyncMock()

        r = await client.post(
            f"/admin/actors/{uuid.uuid4()}/merge",
            json={"duplicate_id": str(uuid.uuid4())},
            # no X-EB-Actor-Id header
        )

        assert r.status_code == 401
        container.actor_registry.merge_actors.assert_not_called()


# ---------------------------------------------------------------------------
# PUT /admin/actors/{actor_id}/organization
# ---------------------------------------------------------------------------

class TestSetActorOrganization:
    async def test_set_org_writes_org_id_property(self, client, container, mock_graph):
        """Happy path SET: verifies org existence, resolves the actor, and
        persists ``org_id`` via ``register_actor`` (property write, not an edge).
        Response echoes ``status='set'``."""
        _enable_bootstrap(container)  # register_actor is a bootstrap action
        actor_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())

        # get_entity is called for both the actor gateway check (skipped: gw="")
        # and the org existence check -> return a truthy org entity.
        mock_graph.get_entity = AsyncMock(
            return_value={"eb_id": org_id, "gateway_id": ""}
        )
        actor = _make_actor(actor_id, org_id=None)
        container.actor_registry.resolve_actor = AsyncMock(return_value=actor)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": org_id},
            headers=_admin_headers(),
        )

        assert r.status_code == 200
        data = r.json()
        assert data == {"actor_id": actor_id, "org_id": org_id, "status": "set"}
        # Org existence was verified.
        mock_graph.get_entity.assert_any_await(org_id)
        # Persisted the org_id onto the actor node.
        container.actor_registry.register_actor.assert_awaited_once()
        persisted = container.actor_registry.register_actor.await_args.args[0]
        assert persisted.org_id == uuid.UUID(org_id)

    async def test_clear_org_sets_org_id_none(self, client, container, mock_graph):
        """Clearing membership (empty org_id) sets ``org_id=None`` and skips the
        org existence lookup. Response echoes ``status='cleared'``."""
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        mock_graph.get_entity = AsyncMock(return_value=None)  # actor gw check skips
        actor = _make_actor(actor_id, org_id=uuid.uuid4())
        container.actor_registry.resolve_actor = AsyncMock(return_value=actor)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": ""},
            headers=_admin_headers(),
        )

        assert r.status_code == 200
        assert r.json() == {"actor_id": actor_id, "org_id": None, "status": "cleared"}
        persisted = container.actor_registry.register_actor.await_args.args[0]
        assert persisted.org_id is None

    async def test_clear_org_with_null_body(self, client, container, mock_graph):
        """A literal ``null`` org_id also clears membership (mirrors empty
        string; ``SetActorOrgRequest.org_id`` defaults to None)."""
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        mock_graph.get_entity = AsyncMock(return_value=None)
        actor = _make_actor(actor_id, org_id=uuid.uuid4())
        container.actor_registry.resolve_actor = AsyncMock(return_value=actor)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": None},
            headers=_admin_headers(),
        )

        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        assert r.json()["org_id"] is None

    async def test_set_org_whitespace_only_is_treated_as_clear(
        self, client, container, mock_graph
    ):
        """A whitespace-only org_id strips to empty -> treated as a clear (no org
        existence lookup, status='cleared')."""
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        mock_graph.get_entity = AsyncMock(return_value=None)
        actor = _make_actor(actor_id, org_id=uuid.uuid4())
        container.actor_registry.resolve_actor = AsyncMock(return_value=actor)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": "   "},
            headers=_admin_headers(),
        )

        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        # No org lookup happened for a clear (get_entity only called for the
        # actor gateway check with the actor_id, never with a stripped org).
        persisted = container.actor_registry.register_actor.await_args.args[0]
        assert persisted.org_id is None

    async def test_set_org_missing_organization_returns_404(
        self, client, container, mock_graph
    ):
        """SET against a non-existent org -> 404 before the actor is resolved."""
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        mock_graph.get_entity = AsyncMock(return_value=None)  # org not found
        container.actor_registry.resolve_actor = AsyncMock()
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": org_id},
            headers=_admin_headers(),
        )

        assert r.status_code == 404
        assert "Organization not found" in r.json()["detail"]
        # No persistence on the org-not-found path. (resolve_actor is NOT
        # asserted here because AuthMiddleware also resolves the *caller* via
        # the same registry method during identity resolution.)
        container.actor_registry.register_actor.assert_not_called()

    async def test_set_org_actor_not_found_returns_404(
        self, client, container, mock_graph
    ):
        """Org exists but the actor is missing -> 404, no persistence."""
        _enable_bootstrap(container)
        actor_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        mock_graph.get_entity = AsyncMock(
            return_value={"eb_id": org_id, "gateway_id": ""}
        )
        container.actor_registry.resolve_actor = AsyncMock(return_value=None)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{actor_id}/organization",
            json={"org_id": org_id},
            headers=_admin_headers(),
        )

        assert r.status_code == 404
        assert "Actor not found" in r.json()["detail"]
        container.actor_registry.register_actor.assert_not_called()

    async def test_set_org_bad_actor_uuid_returns_422(
        self, client, container, mock_graph
    ):
        """A non-UUID actor_id (path) fails ``uuid.UUID()`` -> 422. Uses a clear
        (empty org_id) so the 422 is unambiguously from the actor id parse."""
        _enable_bootstrap(container)
        mock_graph.get_entity = AsyncMock(return_value=None)
        container.actor_registry.resolve_actor = AsyncMock()
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            "/admin/actors/not-a-uuid/organization",
            json={"org_id": ""},
            headers=_admin_headers(),
        )

        assert r.status_code == 422
        container.actor_registry.register_actor.assert_not_called()

    async def test_set_org_requires_auth_in_non_bootstrap_mode(
        self, client, container, mock_graph
    ):
        """No caller identity + non-bootstrap -> 401 before any persistence."""
        _disable_bootstrap(container)
        mock_graph.get_entity = AsyncMock(return_value=None)
        container.actor_registry.register_actor = AsyncMock()

        r = await client.put(
            f"/admin/actors/{uuid.uuid4()}/organization",
            json={"org_id": str(uuid.uuid4())},
            # no X-EB-Actor-Id header
        )

        assert r.status_code == 401
        container.actor_registry.register_actor.assert_not_called()
