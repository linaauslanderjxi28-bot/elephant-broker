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

import asyncio
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
    # Soft-deactivated actors (merged duplicates, offboarded operators) never
    # authorize: degrade to 0 so ``require_authority()`` thresholds reject
    # them — defense-in-depth alongside the SuperTokens session revocation in
    # ``set_actor_status`` and the hard 403 in ``check_authority``.
    if getattr(actor, "active", True) is False:
        return 0
    return int(getattr(actor, "authority_level", 0) or 0)


def _dashboard_handle(st_user_id: str) -> str:
    """The stable, per-user handle that anchors a dashboard actor to its ST id."""
    return f"dashboard:{st_user_id}"


# First-login provisioning is check-then-act (lookup -> create). The dashboard
# fires several API requests in parallel right after login; without
# serialization each of them runs the create path before any has persisted the
# mapping, minting one duplicate actor per request (observed: 3 actors within
# 45ms). Single-tenant-per-process (R2-P1.1) makes an in-process per-user lock
# sufficient; the deterministic actor id in the create path covers the residual
# cross-process window. Bounded by distinct dashboard users per process.
_provision_locks: dict[str, asyncio.Lock] = {}


async def _fetch_st_user_display_name(st_user_id: str) -> str | None:
    """Best-effort human display name for a SuperTokens user.

    Preference order: an explicit name in user metadata → first/last name →
    the account email (from metadata, then the emailpassword recipe, then the
    generic user lookup). Returns ``None`` when nothing usable is found (or the
    SDK is unavailable) so the caller keeps its placeholder fallback.

    Every lookup is lazy-imported and guarded — ``supertokens_python`` is a
    HEAVY, OPTIONAL dependency and may be any of several SDK generations, so we
    probe multiple APIs and never let a failure propagate.
    """
    # 1. usermetadata — apps commonly persist a name/email here.
    try:
        from supertokens_python.recipe.usermetadata.asyncio import (
            get_user_metadata,
        )

        meta = (await get_user_metadata(st_user_id)).metadata or {}
        for key in ("display_name", "name", "full_name"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        first = str(meta.get("first_name", "") or "").strip()
        last = str(meta.get("last_name", "") or "").strip()
        if first or last:
            return f"{first} {last}".strip()
        email = meta.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    except Exception as exc:  # noqa: BLE001 - ST unavailable / no metadata
        logger.debug("usermetadata name lookup unavailable for %s: %s", st_user_id, exc)

    # 2. emailpassword recipe user record (older SDK generations).
    try:
        from supertokens_python.recipe.emailpassword.asyncio import (
            get_user_by_id,
        )

        user = await get_user_by_id(st_user_id)
        email = getattr(user, "email", None)
        if isinstance(email, str) and email.strip():
            return email.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("emailpassword get_user_by_id unavailable for %s: %s", st_user_id, exc)

    # 3. Generic user lookup (newer SDK generations expose `.emails`).
    try:
        from supertokens_python.asyncio import get_user

        user = await get_user(st_user_id)
        emails = getattr(user, "emails", None)
        if emails:
            first_email = emails[0]
            if isinstance(first_email, str) and first_email.strip():
                return first_email.strip()
        email = getattr(user, "email", None)
        if isinstance(email, str) and email.strip():
            return email.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("generic get_user unavailable for %s: %s", st_user_id, exc)

    return None


async def _persist_actor_mapping(st_user_id: str, actor_id: str) -> None:
    """Persist ``eb_actor_id`` into SuperTokens user metadata (best-effort)."""
    try:
        from supertokens_python.recipe.usermetadata.asyncio import (
            update_user_metadata,
        )

        await update_user_metadata(st_user_id, {"eb_actor_id": actor_id})
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not persist eb_actor_id metadata for %s: %s", st_user_id, exc)


async def _maybe_backfill_display_name(
    registry: Any, actor_id: str, st_user_id: str
) -> None:
    """RC-9 backfill: upgrade a placeholder ``dashboard:<uuid>`` display name.

    Existing dashboard actors were created with ``display_name`` set to the raw
    ``dashboard:{st_user_id}`` handle. On any subsequent login we self-heal that
    row from the SuperTokens email/name. Cheap and idempotent — once a real name
    is set the prefix guard short-circuits before any write.
    """
    if registry is None:
        return
    try:
        actor = await registry.resolve_actor(uuid.UUID(str(actor_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("backfill resolve failed for actor %s: %s", actor_id, exc)
        return
    if actor is None:
        return
    current = (getattr(actor, "display_name", "") or "").strip()
    if current and not current.startswith("dashboard:"):
        return  # already has a real display name
    real = await _fetch_st_user_display_name(st_user_id)
    if not real or real == current:
        return
    try:
        actor.display_name = real
        await registry.register_actor(actor)
        logger.info("Backfilled display_name for actor %s -> %r", actor_id, real)
    except Exception as exc:  # noqa: BLE001
        logger.debug("display_name backfill failed for %s: %s", actor_id, exc)


async def _lookup_mapped_actor_id(st_user_id: str) -> str | None:
    """The ``eb_actor_id`` mapping in ST user metadata (fast path, no graph)."""
    try:
        from supertokens_python.recipe.usermetadata.asyncio import (
            get_user_metadata,
        )

        meta = await get_user_metadata(st_user_id)
        existing = (meta.metadata or {}).get("eb_actor_id")
        if existing:
            return str(existing)
    except Exception as exc:  # ST not installed / not configured / no metadata
        logger.debug("usermetadata lookup unavailable for %s: %s", st_user_id, exc)
    return None


async def resolve_actor_from_st_user(container: Any, st_user_id: str) -> str | None:
    """Map a SuperTokens user id to an EB actor id — idempotently.

    Resolution order (actors-orgs-2 fix — a dashboard user maps to EXACTLY ONE
    actor, no matter how many times the metadata write has previously failed):

    1. SuperTokens user metadata ``eb_actor_id`` (fast path, no graph).
    2. An existing actor whose stable handle is ``dashboard:{st_user_id}``
       (authoritative fallback: even when the metadata write failed on a prior
       login, we find and reuse that actor instead of creating a duplicate, and
       repair the missing metadata mapping).
    3. Only when neither exists: create a NEW actor with a real ``display_name``
       resolved from the SuperTokens email/name (actors-orgs-15 / auth-5 /
       cross-cutting-4), and persist the mapping.

    Steps 2-3 run under a per-user in-process lock with a re-check of step 1
    after acquisition, and the created actor's id is
    ``deterministic_uuid_from(handle)`` (UUID v5, the same scheme agent actors
    use for ``agent_key``). Together these close the first-login race where the
    dashboard's parallel request burst minted one duplicate actor per request:
    concurrent callers serialize on the lock, and any residual double-create
    (multi-process, or a transient graph failure in step 2) MERGEs the same
    Neo4j node instead of inserting a new one.

    Degrades gracefully when SuperTokens or the registry is unavailable —
    returns ``None`` so the caller falls through to anonymous.
    """
    handle = _dashboard_handle(st_user_id)
    registry = getattr(container, "actor_registry", None)

    # 1. Metadata mapping — read-only, safe outside the lock.
    mapped_id = await _lookup_mapped_actor_id(st_user_id)

    if mapped_id is None:
        lock = _provision_locks.setdefault(st_user_id, asyncio.Lock())
        async with lock:
            # Re-check now that we hold the lock: a concurrent request may have
            # provisioned (and persisted the mapping) while we waited.
            mapped_id = await _lookup_mapped_actor_id(st_user_id)

            # 2. Fall back to the stable handle so a failed metadata write
            #    never produces a second actor on the next login. Reuse the
            #    existing node and repair the mapping we were missing.
            if (
                mapped_id is None
                and registry is not None
                and hasattr(registry, "resolve_by_handle")
            ):
                try:
                    existing_actor = await registry.resolve_by_handle(handle)
                except Exception as exc:  # noqa: BLE001
                    existing_actor = None
                    logger.debug("resolve_by_handle failed for %s: %s", handle, exc)
                if existing_actor is not None:
                    mapped_id = str(existing_actor.id)
                    await _persist_actor_mapping(st_user_id, mapped_id)

            # 3. First login: create an actor with a real display name and a
            #    handle-derived deterministic id, and remember the mapping.
            if mapped_id is None:
                if registry is None:
                    return None
                display_name = await _fetch_st_user_display_name(st_user_id) or handle
                try:
                    from elephantbroker.schemas.actor import ActorRef, ActorType

                    actor = ActorRef(
                        id=deterministic_uuid_from(handle),
                        type=ActorType.HUMAN_COORDINATOR,
                        display_name=display_name,
                        handles=[handle],
                        authority_level=0,
                        gateway_id=getattr(container, "gateway_id", "") or "",
                    )
                    stored = await registry.register_actor(actor)
                    mapped_id = str(stored.id)
                except Exception as exc:
                    logger.warning(
                        "Failed to create actor for ST user %s: %s", st_user_id, exc
                    )
                    return None
                await _persist_actor_mapping(st_user_id, mapped_id)

    # Self-heal a placeholder display name on the resolved actor.
    await _maybe_backfill_display_name(registry, mapped_id, st_user_id)
    return mapped_id


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
