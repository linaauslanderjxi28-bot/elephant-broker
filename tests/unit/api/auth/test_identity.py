"""Unit tests for :func:`resolve_identity` and the ``AuthIdentity`` model.

Exercises the credential precedence (SuperTokens > API key > gateway identity >
actor header > anonymous), cross-tenant API-key rejection, and authority
resolution. External deps (SuperTokens, DB) are mocked — SuperTokens is simply
not installed in the test env, so that branch falls through to ``None``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import Headers

from elephantbroker.api.auth.api_key_store import ApiKeyRecord
from elephantbroker.api.auth.identity import (
    AuthIdentity,
    AuthMethod,
    resolve_identity,
)
from elephantbroker.runtime.identity import deterministic_uuid_from
from elephantbroker.schemas.actor import ActorRef, ActorType


def _request(headers: dict | None = None, *, gateway_id: str = "gw1"):
    """Build a minimal request stand-in for resolve_identity."""
    state = SimpleNamespace(gateway_id=gateway_id)
    return SimpleNamespace(headers=Headers(headers or {}), state=state)


def _container(
    *,
    api_key_store=None,
    actor_registry=None,
    bootstrap: bool = False,
):
    c = SimpleNamespace()
    c.api_key_store = api_key_store
    c.actor_registry = actor_registry
    c.gateway_id = "gw1"
    c.check_bootstrap_mode = AsyncMock(return_value=bootstrap)
    return c


def _key_store(record: ApiKeyRecord | None):
    store = AsyncMock()
    store.validate = AsyncMock(return_value=record)
    return store


def _registry(actor: ActorRef | None):
    reg = AsyncMock()
    reg.resolve_actor = AsyncMock(return_value=actor)
    return reg


class TestAuthIdentityModel:
    def test_defaults_anonymous(self):
        ident = AuthIdentity()
        assert ident.method is AuthMethod.ANONYMOUS
        assert ident.is_authenticated is False
        assert ident.auth_method == "anonymous"

    def test_auth_method_mirror(self):
        ident = AuthIdentity(method=AuthMethod.API_KEY)
        assert ident.auth_method == "api_key"
        assert ident.is_authenticated is True


class TestApiKeyPath:
    async def test_valid_api_key(self):
        record = ApiKeyRecord(
            key_id="k1", gateway_id="gw1", label="l", key_prefix="eb_ak_ab",
            authority_level=70, actor_id="actor-x",
        )
        container = _container(api_key_store=_key_store(record))
        req = _request({"X-EB-API-Key": "eb_ak_secret"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.API_KEY
        assert ident.authority_level == 70
        assert ident.api_key_id == "k1"
        assert ident.actor_id == "actor-x"
        assert ident.gateway_id == "gw1"

    async def test_lowercase_header_variant(self):
        record = ApiKeyRecord(
            key_id="k1", gateway_id="gw1", label="l", key_prefix="eb_ak_ab",
            authority_level=10,
        )
        container = _container(api_key_store=_key_store(record))
        req = _request({"X-EB-Api-Key": "eb_ak_secret"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.API_KEY

    async def test_cross_tenant_key_rejected(self):
        """A key whose stored gateway differs from the request → treated anon."""
        record = ApiKeyRecord(
            key_id="k1", gateway_id="OTHER", label="l", key_prefix="eb_ak_ab",
            authority_level=90,
        )
        container = _container(api_key_store=_key_store(record))
        req = _request({"X-EB-API-Key": "eb_ak_secret"}, gateway_id="gw1")
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.ANONYMOUS
        assert ident.authority_level == 0

    async def test_invalid_key_falls_through_to_anonymous(self):
        container = _container(api_key_store=_key_store(None))
        req = _request({"X-EB-API-Key": "eb_ak_bad"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.ANONYMOUS

    async def test_no_key_store_skips_path(self):
        container = _container(api_key_store=None)
        req = _request({"X-EB-API-Key": "eb_ak_secret"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.ANONYMOUS


class TestGatewayIdentityPath:
    async def test_agent_key_header(self):
        container = _container()
        req = _request({"X-EB-Agent-Key": "gw1:main"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.GATEWAY_IDENTITY
        assert ident.agent_key == "gw1:main"
        assert ident.actor_id == str(deterministic_uuid_from("gw1:main"))
        assert ident.authority_level == 0

    async def test_api_key_wins_over_agent_key(self):
        record = ApiKeyRecord(
            key_id="k1", gateway_id="gw1", label="l", key_prefix="eb_ak_ab",
            authority_level=50,
        )
        container = _container(api_key_store=_key_store(record))
        req = _request({"X-EB-API-Key": "eb_ak_secret", "X-EB-Agent-Key": "gw1:main"})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.API_KEY


class TestActorHeaderPath:
    async def test_actor_header_resolves_authority(self):
        actor_uuid = str(uuid.uuid4())
        actor = ActorRef(
            type=ActorType.HUMAN_COORDINATOR, display_name="op", authority_level=80,
        )
        container = _container(actor_registry=_registry(actor))
        req = _request({"X-EB-Actor-Id": actor_uuid})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.ACTOR_HEADER
        assert ident.actor_id == actor_uuid
        assert ident.authority_level == 80

    async def test_actor_header_unknown_actor_zero_authority(self):
        container = _container(actor_registry=_registry(None))
        req = _request({"X-EB-Actor-Id": str(uuid.uuid4())})
        ident = await resolve_identity(req, container)
        assert ident.method is AuthMethod.ACTOR_HEADER
        assert ident.authority_level == 0


class TestAnonymousAndBootstrap:
    async def test_anonymous_when_no_credentials(self):
        container = _container()
        ident = await resolve_identity(_request({}), container)
        assert ident.method is AuthMethod.ANONYMOUS
        assert ident.gateway_id == "gw1"

    async def test_bootstrap_flag_propagates(self):
        container = _container(bootstrap=True)
        ident = await resolve_identity(_request({}), container)
        assert ident.is_bootstrap is True

    async def test_bootstrap_false_by_default(self):
        container = _container(bootstrap=False)
        ident = await resolve_identity(_request({}), container)
        assert ident.is_bootstrap is False
