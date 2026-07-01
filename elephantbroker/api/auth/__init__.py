"""Dashboard authentication package (Phase 11).

Exposes the resolved-principal model, credential resolution, the API-key store,
the ``require_authority`` dependency, and SuperTokens initialization. Heavy /
optional dependencies (``supertokens_python``) are lazy-imported inside the
relevant functions, so importing this package never requires them.
"""
from __future__ import annotations

from elephantbroker.api.auth.api_key_store import ApiKeyRecord, ApiKeyStore
from elephantbroker.api.auth.identity import (
    AuthIdentity,
    AuthMethod,
    get_identity,
    require_authority,
    resolve_actor_from_st_user,
    resolve_identity,
)

__all__ = [
    "ApiKeyRecord",
    "ApiKeyStore",
    "AuthIdentity",
    "AuthMethod",
    "get_identity",
    "require_authority",
    "resolve_actor_from_st_user",
    "resolve_identity",
]
