"""Resolved-principal model and credential-resolution helpers (Phase 11 auth).

``AuthIdentity`` is the single principal object produced by ``AuthMiddleware``
for every request. It unifies the three supported authentication methods:

    * SuperTokens session cookie   → dashboard users
    * ``X-EB-API-Key`` header      → CLI / programmatic callers
    * ``X-EB-Agent-Key`` header    → TS plugins (gateway identity, existing)

plus the legacy ``X-EB-Actor-Id`` admin path and the anonymous fallback.

The middleware stamps the result on BOTH ``request.state.identity`` (the name
used by the ``require_authority`` dependency and the Phase 11 dashboard routes)
and ``request.state.auth_identity`` (the name used by the plan's route handlers).
``resolve_identity`` never raises — route-level enforcement is done by
``require_authority``.
"""
from __future__ import annotations

import enum
import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from elephantbroker.runtime.identity import deterministic_uuid_from

logger = logging.getLogger(__name__)


class AuthMethod(enum.StrEnum):
    """How a request's principal was authenticated."""

    ANONYMOUS = "anonymous"  # no credential (bootstrap / dev / internal)
    API_KEY = "api_key"  # authenticated via X-EB-API-Key
    ACTOR_HEADER = "actor_header"  # X-EB-Actor-Id only (legacy admin path)
    SUPERTOKENS_SESSION = "supertokens_session"  # dashboard session cookie
    GATEWAY_IDENTITY = "gateway_identity"  # X-EB-Agent-Key (TS plugins)


class AuthIdentity(BaseModel):
    """Resolved caller identity produced by ``AuthMiddleware``.

    Superset of the fields required by both the backend integration contract
    (``method``, ``authority_level``, ``api_key_id``, ``is_bootstrap``) and the
    Phase 11 plan (``auth_method``, ``supertokens_user_id``, ``agent_key``,
    ``permissions``). ``auth_method`` is a read-only mirror of ``method`` for
    callers that expect the plan's field name.
    """

    method: AuthMethod = AuthMethod.ANONYMOUS
    gateway_id: str = ""
    actor_id: str | None = None  # uuid string when known
    authority_level: int = 0  # effective level for require_authority()
    api_key_id: str | None = None  # set when method == API_KEY
    supertokens_user_id: str | None = None  # set when method == SUPERTOKENS_SESSION
    agent_key: str | None = None  # set when method == GATEWAY_IDENTITY
    permissions: list[str] = []
    is_bootstrap: bool = False  # mirrors container bootstrap mode

    @property
    def auth_method(self) -> str:
        """Plan-compatible alias for ``method`` (returns the string value)."""
        return self.method.value

    @property
    def is_authenticated(self) -> bool:
        """True when the request carried a recognized credential."""
        return self.method is not AuthMethod.ANONYMOUS


# ---------------------------------------------------------------------------
# Actor authority resolution
# ---------------------------------------------------------------------------

async def _authority_for_actor(container: Any, actor_id: str | None) -> int:
    """Resolve an actor's authority_level via the registry (best-effort)."""
    if not actor_id:
        return 0
    registry = getattr(container, "actor_registry", None)
    if registry is None:
        return 0
    try:
        aid = actor_id if isinstance(actor_id, uuid.UUID) else uuid.UUID(str(actor_id))
    except (ValueError, AttributeError, TypeError):
        return 0
    try:
        actor = await registry.resolve_actor(aid)
    except Exception as exc:  # registry not wired / graph unavailable
        logger.debug("authority resolution failed for actor=%s: %s", actor_id, exc)
        return 0
    if actor is None:
        return 0
    return int(getattr(actor, "authority_level", 0) or 0)


async def resolve_actor_from_st_user(container: Any, st_user_id: str) -> str | None:
    """Map a SuperTokens user id to an EB actor id.

    On first login an ``ActorRef`` is created with handle ``dashboard:{st_user_id}``
    and its id is persisted into SuperTokens user metadata under ``eb_actor_id``.
    Subsequent logins resolve the mapping straight from metadata (no graph
    lookup). Degrades gracefully when SuperTokens or the registry is unavailable
    — returns ``None`` so the caller falls through to anonymous.
    """
    # 1. Try the metadata mapping first (fast path, no graph).
    try:
        from supertokens_python.recipe.usermetadata.asyncio import get_user_metadata

        meta = await get_user_metadata(st_user_id)
        existing = (meta.metadata or {}).get("eb_actor_id")
        if existing:
            return str(existing)
    except Exception as exc:  # ST not installed / not configured / no metadata
        logger.debug("usermetadata lookup unavailable for %s: %s", st_user_id, exc)

    # 2. First login: create an actor and remember the mapping.
    registry = getattr(container, "actor_registry", None)
    if registry is None:
        return None
    try:
        from elephantbroker.schemas.actor import ActorRef, ActorType

        actor = ActorRef(
            type=ActorType.HUMAN_OPERATOR,
            display_name=f"dashboard:{st_user_id}",
            handles=[f"dashboard:{st_user_id}"],
            authority_level=0,
            gateway_id=getattr(container, "gateway_id", "") or "",
        )
        stored = await registry.register_actor(actor)
        actor_id = str(stored.id)
    except Exception as exc:
        logger.warning("Failed to create actor for ST user %s: %s", st_user_id, exc)
        return None

    # 3. Persist the mapping into user metadata (best-effort).
    try:
        from supertokens_python.recipe.usermetadata.asyncio import update_user_metadata

        await update_user_metadata(st_user_id, {"eb_actor_id": actor_id})
    except Exception as exc:
        logger.debug("could not persist eb_actor_id metadata for %s: %s", st_user_id, exc)

    return actor_id


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

async def resolve_identity(request: Request, container: Any) -> AuthIdentity:
    """Resolve the request principal. Never raises (except a re-raised token
    refresh signal handled by the middleware).

    Precedence: SuperTokens session > ``X-EB-API-Key`` > ``X-EB-Agent-Key``
    (gateway identity) > ``X-EB-Actor-Id`` header > anonymous.

    ``gateway_id`` ALWAYS comes from ``request.state.gateway_id`` (already
    validated and tenant-checked by ``GatewayIdentityMiddleware``). An API key
    whose stored ``gateway_id`` differs from the request's is treated as
    anonymous (cross-tenant key rejected).
    """
    gateway_id = getattr(request.state, "gateway_id", "") or ""
    is_bootstrap = False
    if hasattr(container, "check_bootstrap_mode"):
        try:
            is_bootstrap = await container.check_bootstrap_mode()
        except Exception:
            is_bootstrap = False

    # 1. SuperTokens session cookie.
    session_identity = await _try_supertokens(request)
    if session_identity is not None:
        st_user_id = session_identity
        actor_id = await resolve_actor_from_st_user(container, st_user_id)
        level = await _authority_for_actor(container, actor_id)
        return AuthIdentity(
            method=AuthMethod.SUPERTOKENS_SESSION,
            gateway_id=gateway_id,
            actor_id=actor_id,
            authority_level=level,
            supertokens_user_id=st_user_id,
            is_bootstrap=is_bootstrap,
        )

    # 2. API key.
    api_key = request.headers.get("X-EB-API-Key") or request.headers.get("X-EB-Api-Key")
    store = getattr(container, "api_key_store", None)
    if api_key and store is not None:
        try:
            record = await store.validate(api_key)
        except Exception as exc:
            logger.debug("api key validation error: %s", exc)
            record = None
        if record is not None and record.gateway_id == gateway_id:
            return AuthIdentity(
                method=AuthMethod.API_KEY,
                gateway_id=gateway_id,
                actor_id=record.actor_id,
                authority_level=record.authority_level,
                api_key_id=record.key_id,
                is_bootstrap=is_bootstrap,
            )

    # 3. Gateway identity (X-EB-Agent-Key) — TS plugins.
    agent_key = request.headers.get("X-EB-Agent-Key") or getattr(
        request.state, "agent_key", ""
    )
    if agent_key:
        return AuthIdentity(
            method=AuthMethod.GATEWAY_IDENTITY,
            gateway_id=gateway_id,
            actor_id=str(deterministic_uuid_from(agent_key)),
            authority_level=0,
            agent_key=agent_key,
            is_bootstrap=is_bootstrap,
        )

    # 4. Legacy actor header path.
    actor_header = request.headers.get("X-EB-Actor-Id") or getattr(
        request.state, "actor_id", ""
    )
    if actor_header:
        level = await _authority_for_actor(container, actor_header)
        return AuthIdentity(
            method=AuthMethod.ACTOR_HEADER,
            gateway_id=gateway_id,
            actor_id=actor_header,
            authority_level=level,
            is_bootstrap=is_bootstrap,
        )

    # 5. Anonymous.
    return AuthIdentity(
        method=AuthMethod.ANONYMOUS,
        gateway_id=gateway_id,
        is_bootstrap=is_bootstrap,
    )


async def _try_supertokens(request: Request) -> str | None:
    """Return the SuperTokens user id for a valid session, else ``None``.

    Re-raises ``TryRefreshTokenError`` so the middleware can return 401. All
    other SuperTokens conditions (no session, unauthorised, SDK not installed /
    not initialized) resolve to ``None`` — the caller falls through to the next
    auth method.
    """
    try:
        from supertokens_python.recipe.session.asyncio import get_session
        from supertokens_python.recipe.session.exceptions import (
            TryRefreshTokenError,
            UnauthorisedError,
        )
    except Exception:
        # SDK not installed — SuperTokens auth simply unavailable.
        return None

    try:
        session = await get_session(request, session_required=False)
    except TryRefreshTokenError:
        raise
    except UnauthorisedError:
        return None
    except Exception as exc:
        logger.debug("supertokens session check failed: %s", exc)
        return None

    if session is None:
        return None
    try:
        return session.get_user_id()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# require_authority dependency
# ---------------------------------------------------------------------------

def get_identity(request: Request) -> AuthIdentity:
    """Read the resolved identity stamped by ``AuthMiddleware`` (or anonymous)."""
    return getattr(request.state, "identity", None) or AuthIdentity()


def require_authority(min_level: int):
    """FastAPI dependency factory enforcing ``authority_level >= min_level``.

    Bootstrap mode bypasses the check. Raises ``HTTPException(403)`` otherwise.

    Usage::

        @router.get("/facts", dependencies=[Depends(require_authority(70))])
        async def list_facts(...): ...

        async def handler(identity: AuthIdentity = Depends(require_authority(70))):
            ...
    """

    async def _dep(request: Request) -> AuthIdentity:
        identity = get_identity(request)
        container = getattr(request.app.state, "container", None)
        bootstrap = False
        if container is not None and hasattr(container, "check_bootstrap_mode"):
            try:
                bootstrap = await container.check_bootstrap_mode()
            except Exception:
                bootstrap = False
        if bootstrap or identity.is_bootstrap:
            return identity
        if identity.authority_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Requires authority_level >= {min_level} "
                    f"(have {identity.authority_level})"
                ),
            )
        return identity

    return _dep
