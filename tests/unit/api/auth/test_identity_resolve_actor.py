"""Unit tests for the branch-new (Phase 11) idempotent ST-user → actor mapping.

Covers the ``actors-orgs-2`` idempotency fix and ``RC-9`` self-heal in
:func:`elephantbroker.api.auth.identity.resolve_actor_from_st_user`:

    1. Reuse the actor id already stored in SuperTokens metadata (fast path).
    2. Reuse + repair via the stable ``dashboard:{st_id}`` handle when the
       metadata write failed on a prior login (no duplicate actor created).
    3. Create a NEW actor with the *real* SuperTokens display name (email/name)
       when neither mapping exists, falling back to the handle placeholder.
    4. ``_maybe_backfill_display_name`` self-heals placeholder display names.
    5. ``_dashboard_handle`` format.
    6. Race fix: concurrent first logins provision exactly ONE actor (per-user
       provisioning lock + deterministic UUID v5 actor id from the handle).

``supertokens_python`` IS installed in this venv, so the source's lazy
``from supertokens_python... import ...`` calls resolve to the real SDK
functions — we mock those functions in place via ``monkeypatch.setattr`` so no
SuperTokens backend / network is ever touched. The actor registry is an
``AsyncMock``.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from elephantbroker.api.auth.identity import (
    _dashboard_handle,
    _maybe_backfill_display_name,
    resolve_actor_from_st_user,
)
from elephantbroker.runtime.identity import deterministic_uuid_from
from elephantbroker.schemas.actor import ActorRef, ActorType

_UM_PATH = "supertokens_python.recipe.usermetadata.asyncio"
_GET_USER_PATH = "supertokens_python.asyncio.get_user"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _container(registry, *, gateway_id: str = "gw1"):
    return SimpleNamespace(actor_registry=registry, gateway_id=gateway_id)


def _registry(*, by_handle=None, resolve=None, register=None):
    reg = AsyncMock()
    reg.resolve_by_handle = AsyncMock(return_value=by_handle)
    reg.resolve_actor = AsyncMock(return_value=resolve)
    reg.register_actor = register or AsyncMock(side_effect=lambda a: a)
    return reg


def _actor(display_name: str, **kw) -> ActorRef:
    return ActorRef(type=ActorType.HUMAN_COORDINATOR, display_name=display_name, **kw)


def _patch_usermetadata(monkeypatch, *, metadata=None, get_raises=False, update=None):
    """Patch the ST usermetadata get/update async functions in place."""
    if get_raises:
        get_mock = AsyncMock(side_effect=RuntimeError("ST unavailable"))
    else:
        get_mock = AsyncMock(return_value=SimpleNamespace(metadata=metadata or {}))
    update_mock = update or AsyncMock()
    monkeypatch.setattr(f"{_UM_PATH}.get_user_metadata", get_mock)
    monkeypatch.setattr(f"{_UM_PATH}.update_user_metadata", update_mock)
    return get_mock, update_mock


def _patch_get_user(monkeypatch, *, emails=None, email=None):
    monkeypatch.setattr(
        _GET_USER_PATH,
        AsyncMock(return_value=SimpleNamespace(emails=emails or [], email=email)),
    )


# ---------------------------------------------------------------------------
# _dashboard_handle
# ---------------------------------------------------------------------------

def test_dashboard_handle_format():
    assert _dashboard_handle("st-abc-123") == "dashboard:st-abc-123"


# ---------------------------------------------------------------------------
# 1. reuse from metadata (fast path)
# ---------------------------------------------------------------------------

async def test_reuse_actor_id_from_metadata(monkeypatch):
    """When ST metadata already carries eb_actor_id, reuse it without any
    handle lookup or actor creation."""
    existing_id = str(uuid.uuid4())
    _patch_usermetadata(monkeypatch, metadata={"eb_actor_id": existing_id})
    # backfill resolve returns an actor with a real name → no write.
    reg = _registry(resolve=_actor("Real Name", id=uuid.UUID(existing_id)))
    container = _container(reg)

    result = await resolve_actor_from_st_user(container, "st-user-1")

    assert result == existing_id
    reg.resolve_by_handle.assert_not_called()  # metadata short-circuits handle path
    reg.register_actor.assert_not_called()  # no create, no backfill write


# ---------------------------------------------------------------------------
# 2. reuse + repair via stable handle (metadata write previously failed)
# ---------------------------------------------------------------------------

async def test_reuse_and_repair_via_handle(monkeypatch):
    """No eb_actor_id in metadata, but an actor exists under the stable handle:
    reuse that actor (no duplicate) and repair the metadata mapping."""
    existing = _actor("Real Person", id=uuid.uuid4())
    _, update_mock = _patch_usermetadata(monkeypatch, metadata={})  # no eb_actor_id
    # resolve_by_handle finds it; backfill resolve returns same (real name → no write)
    reg = _registry(by_handle=existing, resolve=existing)
    container = _container(reg)

    result = await resolve_actor_from_st_user(container, "st-user-2")

    assert result == str(existing.id)
    reg.resolve_by_handle.assert_awaited_once_with("dashboard:st-user-2")
    reg.register_actor.assert_not_called()  # reused, not recreated
    # repair: the missing metadata mapping is written back.
    update_mock.assert_awaited_once_with("st-user-2", {"eb_actor_id": str(existing.id)})


# ---------------------------------------------------------------------------
# 3. create with the real display name (neither mapping exists)
# ---------------------------------------------------------------------------

async def test_create_with_real_display_name_from_email(monkeypatch):
    """First login with no prior mapping: create ONE actor whose display_name is
    the resolved SuperTokens email, anchored by the stable handle."""
    _, update_mock = _patch_usermetadata(
        monkeypatch, metadata={"email": "alice@example.com"}
    )
    reg = _registry(by_handle=None)  # nothing under the handle
    container = _container(reg, gateway_id="gw-xyz")

    result = await resolve_actor_from_st_user(container, "st-user-3")

    reg.register_actor.assert_awaited_once()
    created = reg.register_actor.await_args.args[0]
    assert isinstance(created, ActorRef)
    assert created.display_name == "alice@example.com"
    assert created.handles == ["dashboard:st-user-3"]
    assert created.type is ActorType.HUMAN_COORDINATOR
    assert created.gateway_id == "gw-xyz"
    assert result == str(created.id)
    # id is derived from the handle (UUID v5) so a double-create MERGEs the
    # same graph node instead of minting a duplicate actor.
    assert created.id == deterministic_uuid_from("dashboard:st-user-3")
    # newly-minted mapping persisted to ST metadata.
    update_mock.assert_awaited_once_with("st-user-3", {"eb_actor_id": result})


async def test_create_falls_back_to_handle_when_no_name(monkeypatch):
    """When ST yields no usable name/email, display_name falls back to the
    ``dashboard:{st_id}`` handle placeholder."""
    _patch_usermetadata(monkeypatch, metadata={})  # no name, no email
    _patch_get_user(monkeypatch, emails=[], email=None)  # generic lookup: nothing
    reg = _registry(by_handle=None)
    container = _container(reg)

    result = await resolve_actor_from_st_user(container, "st-user-4")

    created = reg.register_actor.await_args.args[0]
    assert created.display_name == "dashboard:st-user-4"
    assert created.handles == ["dashboard:st-user-4"]
    assert result == str(created.id)


async def test_handle_lookup_error_falls_through_to_create(monkeypatch):
    """A raising resolve_by_handle must not abort resolution — it degrades to
    the create path (safe even if the actor already exists: the deterministic
    handle-derived id makes the create MERGE the existing node, not duplicate)."""
    _patch_usermetadata(monkeypatch, metadata={})
    _patch_get_user(monkeypatch, emails=[], email=None)
    reg = _registry()
    reg.resolve_by_handle = AsyncMock(side_effect=RuntimeError("graph down"))
    container = _container(reg)

    result = await resolve_actor_from_st_user(container, "st-user-5")

    reg.register_actor.assert_awaited_once()
    assert result == str(reg.register_actor.await_args.args[0].id)


async def test_concurrent_first_logins_provision_exactly_one_actor(monkeypatch):
    """The login-burst race: N parallel requests for the same ST user with no
    prior mapping must create exactly ONE actor (per-user lock serializes the
    provisioning; the re-check under the lock sees the persisted mapping)."""
    store: dict[str, dict] = {"st-user-race": {"email": "dana@example.com"}}

    async def fake_get(st_id: str):
        return SimpleNamespace(metadata=dict(store.get(st_id, {})))

    async def fake_update(st_id: str, patch: dict):
        store.setdefault(st_id, {}).update(patch)

    monkeypatch.setattr(f"{_UM_PATH}.get_user_metadata", fake_get)
    monkeypatch.setattr(f"{_UM_PATH}.update_user_metadata", fake_update)

    reg = _registry(by_handle=None)  # graph is empty — true first login
    container = _container(reg)

    results = await asyncio.gather(
        *(resolve_actor_from_st_user(container, "st-user-race") for _ in range(3))
    )

    reg.register_actor.assert_awaited_once()  # ONE create across the burst
    assert len(set(results)) == 1
    assert results[0] == str(deterministic_uuid_from("dashboard:st-user-race"))
    assert store["st-user-race"]["eb_actor_id"] == results[0]


async def test_returns_none_when_registry_absent(monkeypatch):
    """Graceful degradation: no metadata mapping and no registry → None so the
    caller falls through to anonymous."""
    _patch_usermetadata(monkeypatch, get_raises=True)  # ST unavailable
    container = _container(None)

    result = await resolve_actor_from_st_user(container, "st-user-6")

    assert result is None


# ---------------------------------------------------------------------------
# 4. _maybe_backfill_display_name self-heal
# ---------------------------------------------------------------------------

async def test_backfill_upgrades_placeholder_display_name(monkeypatch):
    """A placeholder ``dashboard:<id>`` display name is self-healed from the ST
    email on a later login."""
    aid = uuid.uuid4()
    placeholder = _actor(f"dashboard:{aid}", id=aid)
    _patch_usermetadata(monkeypatch, metadata={"email": "bob@example.com"})
    reg = _registry(resolve=placeholder)

    await _maybe_backfill_display_name(reg, str(aid), "st-user-7")

    assert placeholder.display_name == "bob@example.com"
    reg.register_actor.assert_awaited_once_with(placeholder)


async def test_backfill_noop_when_real_name_present(monkeypatch):
    """A real (non-placeholder) display name short-circuits before any write."""
    aid = uuid.uuid4()
    actor = _actor("Charlie Real", id=aid)
    get_mock, _ = _patch_usermetadata(monkeypatch, metadata={"email": "c@example.com"})
    reg = _registry(resolve=actor)

    await _maybe_backfill_display_name(reg, str(aid), "st-user-8")

    assert actor.display_name == "Charlie Real"
    reg.register_actor.assert_not_called()
    get_mock.assert_not_called()  # never even fetched the ST name


async def test_backfill_noop_when_registry_none():
    """No registry → backfill is a safe no-op (no exception)."""
    assert await _maybe_backfill_display_name(None, str(uuid.uuid4()), "st-x") is None
