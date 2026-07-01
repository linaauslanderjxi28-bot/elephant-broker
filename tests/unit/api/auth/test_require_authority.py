"""Unit tests for the :func:`require_authority` FastAPI dependency factory.

Verifies the gating semantics directly against the inner dependency callable
(no HTTP stack needed): pass at/above threshold, 403 below, and bootstrap
bypass (both container-level and identity-level).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from elephantbroker.api.auth.identity import (
    AuthIdentity,
    AuthMethod,
    require_authority,
)


def _request(identity: AuthIdentity | None, *, bootstrap: bool = False):
    container = SimpleNamespace(check_bootstrap_mode=AsyncMock(return_value=bootstrap))
    return SimpleNamespace(
        state=SimpleNamespace(identity=identity),
        app=SimpleNamespace(state=SimpleNamespace(container=container)),
    )


def _identity(level: int, *, is_bootstrap: bool = False):
    return AuthIdentity(
        method=AuthMethod.API_KEY, authority_level=level, is_bootstrap=is_bootstrap
    )


class TestRequireAuthority:
    async def test_passes_when_level_equals_threshold(self):
        dep = require_authority(70)
        ident = await dep(_request(_identity(70)))
        assert ident.authority_level == 70

    async def test_passes_when_level_above_threshold(self):
        dep = require_authority(70)
        ident = await dep(_request(_identity(90)))
        assert ident.authority_level == 90

    async def test_rejects_when_below_threshold(self):
        dep = require_authority(70)
        with pytest.raises(HTTPException) as exc:
            await dep(_request(_identity(10)))
        assert exc.value.status_code == 403
        assert "70" in str(exc.value.detail)

    async def test_rejects_anonymous(self):
        dep = require_authority(50)
        with pytest.raises(HTTPException) as exc:
            await dep(_request(AuthIdentity()))
        assert exc.value.status_code == 403

    async def test_container_bootstrap_bypasses_check(self):
        dep = require_authority(90)
        # Low authority, but container is in bootstrap mode → allowed.
        ident = await dep(_request(_identity(0), bootstrap=True))
        assert ident is not None

    async def test_identity_bootstrap_bypasses_check(self):
        dep = require_authority(90)
        ident = await dep(_request(_identity(0, is_bootstrap=True)))
        assert ident is not None

    async def test_missing_identity_defaults_anonymous_rejected(self):
        dep = require_authority(50)
        # request.state has no identity → get_identity() returns anonymous.
        req = _request(None)
        with pytest.raises(HTTPException) as exc:
            await dep(req)
        assert exc.value.status_code == 403
