"""Auth management routes (Phase 11) — API keys + identity introspection.

These custom routes live alongside the SuperTokens auto-generated ``/auth/*``
endpoints (signin, signup, signout, session refresh). They let an authenticated
dashboard user (or the bootstrap admin) mint, list, and revoke API keys, and
introspect their own resolved identity.

Authentication is resolved upstream by ``AuthMiddleware`` and read from
``request.state.identity``. Key creation is permitted for authenticated callers
and, while the gateway is still un-bootstrapped, for the first (anonymous)
caller — after which ``bootstrap_complete`` disables the self-bootstrap path.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from elephantbroker.api.auth.identity import AuthIdentity, get_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateApiKeyRequest(BaseModel):
    label: str = Field(min_length=1)
    # Authority granted to requests authenticated by this key. Capped at the
    # creator's own authority_level (see route). Ignored during bootstrap.
    authority_level: int = Field(default=0, ge=0)


def _gateway_id(request: Request) -> str:
    """Canonical gateway_id read — middleware always stamps a string."""
    return getattr(request.state, "gateway_id", "") or ""


async def _bootstrap_allowed(request: Request) -> bool:
    """True when the self-bootstrap first-admin path is still open.

    Open while EITHER the container reports bootstrap mode (empty actor graph)
    OR ``dashboard_auth.bootstrap_complete`` is False.
    """
    container = getattr(request.app.state, "container", None)
    if container is None:
        return False
    # Explicit config flag wins when present.
    cfg = getattr(getattr(container, "config", None), "dashboard_auth", None)
    if cfg is not None and getattr(cfg, "bootstrap_complete", False):
        return False
    if hasattr(container, "check_bootstrap_mode"):
        try:
            return await container.check_bootstrap_mode()
        except Exception:
            return False
    return bool(getattr(container, "_bootstrap_mode", False))


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

@router.post("/api-keys")
async def create_api_key(body: CreateApiKeyRequest, request: Request):
    """Create an API key bound to the caller's actor. Plaintext returned once."""
    container = getattr(request.app.state, "container", None)
    store = getattr(container, "api_key_store", None) if container else None
    if store is None:
        return JSONResponse(
            status_code=503, content={"detail": "API key store not available"}
        )

    identity: AuthIdentity = get_identity(request)
    bootstrap = await _bootstrap_allowed(request)

    if not identity.is_authenticated and not bootstrap:
        raise HTTPException(status_code=401, detail="Authentication required to create API keys")

    # Bootstrap admin gets full authority; otherwise cap the granted level at
    # the creator's own authority_level.
    if bootstrap and not identity.is_authenticated:
        granted = body.authority_level if body.authority_level else 90
        actor_id = identity.actor_id
    else:
        granted = (
            min(body.authority_level, identity.authority_level)
            if body.authority_level
            else identity.authority_level
        )
        actor_id = identity.actor_id

    record, plaintext = await store.create(
        gateway_id=_gateway_id(request),
        label=body.label,
        authority_level=granted,
        actor_id=actor_id,
    )
    # Plaintext is surfaced exactly once.
    return {"key": plaintext, **record.model_dump(mode="json")}


@router.get("/api-keys")
async def list_api_keys(request: Request):
    """List the caller's API keys (masked — no plaintext, no hash)."""
    container = getattr(request.app.state, "container", None)
    store = getattr(container, "api_key_store", None) if container else None
    if store is None:
        return {"keys": []}
    identity: AuthIdentity = get_identity(request)
    records = await store.list_masked(gateway_id=_gateway_id(request))
    # Scope to the caller's own actor unless they are a system admin (>=90).
    if identity.actor_id and identity.authority_level < 90:
        records = [r for r in records if r.actor_id == identity.actor_id]
    return {"keys": [r.model_dump(mode="json") for r in records]}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an API key by its public key_id."""
    container = getattr(request.app.state, "container", None)
    store = getattr(container, "api_key_store", None) if container else None
    if store is None:
        return JSONResponse(
            status_code=503, content={"detail": "API key store not available"}
        )
    ok = await store.revoke(key_id, gateway_id=_gateway_id(request))
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "Key not found"})
    return {"key_id": key_id, "status": "revoked"}


# ---------------------------------------------------------------------------
# Identity introspection
# ---------------------------------------------------------------------------

@router.get("/identity")
async def get_current_identity(request: Request):
    """Return the resolved identity for the current caller.

    Shape: ``{actor_id, display_name, authority_level, org_id, type, auth_method}``.
    """
    identity: AuthIdentity = get_identity(request)
    container = getattr(request.app.state, "container", None)

    display_name: str | None = None
    org_id: str | None = None
    actor_type: str | None = None

    registry = getattr(container, "actor_registry", None) if container else None
    if registry is not None and identity.actor_id:
        try:
            aid = uuid.UUID(str(identity.actor_id))
            actor = await registry.resolve_actor(aid)
        except Exception:
            actor = None
        if actor is not None:
            display_name = getattr(actor, "display_name", None)
            org_id = str(actor.org_id) if getattr(actor, "org_id", None) else None
            actor_type = getattr(getattr(actor, "type", None), "value", None) or (
                str(actor.type) if getattr(actor, "type", None) else None
            )

    return {
        "actor_id": identity.actor_id,
        "display_name": display_name,
        "authority_level": identity.authority_level,
        "org_id": org_id,
        "type": actor_type,
        "auth_method": identity.auth_method,
    }
