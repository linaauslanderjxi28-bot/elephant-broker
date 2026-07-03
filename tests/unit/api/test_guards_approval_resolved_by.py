"""Tests for the branch-new server-side ``resolved_by`` resolution in
``PATCH /guards/approvals/{request_id}`` (``update_approval``).

Branch delta under test (guards.py, gap-3-5): the audit's ``resolved_by`` is now
taken from the authenticated principal — ``get_identity(request).actor_id`` — and
threaded into the approval-queue call as ``approved_by=`` / ``rejected_by=``,
OVERRIDING any client-supplied ``body["resolved_by"]``. When identity is absent
(anonymous, ``actor_id is None``) or ``get_identity`` raises, the code falls back
to the body value.

These assert the value threaded into ``engine._approvals.approve/reject``, NOT the
raw body. All I/O is mocked (approval queue, guard engine, sessions) in the style
of the neighbouring ``test_routes_guards.py``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from elephantbroker.api.auth.identity import AuthIdentity, AuthMethod

GUARDS_MODULE = "elephantbroker.api.routes.guards"


class _FakeApprovalResult:
    """Minimal stand-in for an ``ApprovalRequest`` — only ``model_dump`` is used
    by the route to serialize the response."""

    def model_dump(self, mode: str = "json"):
        return {"status": "resolved"}


def _setup_approvals(container):
    """Wire the container's guard engine with a mocked approval queue.

    Returns the ``approvals`` MagicMock so tests can inspect the exact kwargs
    threaded into ``approve()`` / ``reject()``.
    """
    engine = container.guard_engine
    engine._sessions = {}          # empty -> session_key resolution stays ""
    engine._goals = None           # hasattr(engine, "_goals") -> session_goal_store=None

    approvals = MagicMock()
    approvals.approve = AsyncMock(return_value=_FakeApprovalResult())
    approvals.reject = AsyncMock(return_value=_FakeApprovalResult())
    engine._approvals = approvals
    return approvals


def _patch_identity(monkeypatch, identity):
    """Force ``get_identity`` (as imported into the guards route module) to return
    ``identity`` for the duration of a test."""
    monkeypatch.setattr(f"{GUARDS_MODULE}.get_identity", lambda _request: identity)


# ---------------------------------------------------------------------------
# Server-side identity OVERRIDES the client-supplied body value
# ---------------------------------------------------------------------------

class TestIdentityOverridesBody:
    async def test_approve_uses_identity_actor_id_not_body(self, client, container, monkeypatch):
        approvals = _setup_approvals(container)
        server_actor = str(uuid.uuid4())
        _patch_identity(
            monkeypatch,
            AuthIdentity(method=AuthMethod.SUPERTOKENS_SESSION, actor_id=server_actor),
        )

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved", "agent_id": "a1", "resolved_by": "client-forged"},
        )

        assert r.status_code == 200
        approvals.approve.assert_awaited_once()
        kwargs = approvals.approve.await_args.kwargs
        # The server-resolved actor wins; the client's forged value is ignored.
        assert kwargs["approved_by"] == server_actor
        assert kwargs["approved_by"] != "client-forged"
        # reject must not have been called on the approve path.
        approvals.reject.assert_not_awaited()

    async def test_reject_uses_identity_actor_id_not_body(self, client, container, monkeypatch):
        approvals = _setup_approvals(container)
        server_actor = str(uuid.uuid4())
        _patch_identity(
            monkeypatch,
            AuthIdentity(method=AuthMethod.API_KEY, actor_id=server_actor),
        )

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={
                "status": "rejected",
                "agent_id": "a1",
                "resolved_by": "client-forged",
                "reason": "not allowed",
            },
        )

        assert r.status_code == 200
        approvals.reject.assert_awaited_once()
        kwargs = approvals.reject.await_args.kwargs
        assert kwargs["rejected_by"] == server_actor
        assert kwargs["rejected_by"] != "client-forged"
        assert kwargs["reason"] == "not allowed"
        approvals.approve.assert_not_awaited()

    async def test_identity_actor_id_is_stringified(self, client, container, monkeypatch):
        """A UUID-typed ``actor_id`` on the identity is coerced via ``str(...)``."""
        approvals = _setup_approvals(container)
        actor_uuid = uuid.uuid4()
        # pydantic keeps actor_id as a str field; feed the str form and assert
        # the exact string is threaded through.
        _patch_identity(
            monkeypatch,
            AuthIdentity(method=AuthMethod.SUPERTOKENS_SESSION, actor_id=str(actor_uuid)),
        )

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved", "resolved_by": "ignored"},
        )

        assert r.status_code == 200
        assert approvals.approve.await_args.kwargs["approved_by"] == str(actor_uuid)


# ---------------------------------------------------------------------------
# Fallback to the body value when there is no authenticated actor
# ---------------------------------------------------------------------------

class TestFallsBackToBody:
    async def test_anonymous_identity_falls_back_to_body(self, client, container, monkeypatch):
        approvals = _setup_approvals(container)
        # Anonymous identity => actor_id is None => keep the body value (HITL path).
        _patch_identity(monkeypatch, AuthIdentity())

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved", "resolved_by": "human-approver@hitl"},
        )

        assert r.status_code == 200
        assert approvals.approve.await_args.kwargs["approved_by"] == "human-approver@hitl"

    async def test_reject_anonymous_identity_falls_back_to_body(self, client, container, monkeypatch):
        approvals = _setup_approvals(container)
        _patch_identity(monkeypatch, AuthIdentity())

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "rejected", "resolved_by": "human-approver@hitl", "reason": "no"},
        )

        assert r.status_code == 200
        assert approvals.reject.await_args.kwargs["rejected_by"] == "human-approver@hitl"

    async def test_get_identity_raises_falls_back_to_body(self, client, container, monkeypatch):
        """The identity lookup is best-effort: if ``get_identity`` raises, the
        body value is still used (the ``try/except`` in the diff)."""
        approvals = _setup_approvals(container)

        def _boom(_request):
            raise RuntimeError("identity backend down")

        monkeypatch.setattr(f"{GUARDS_MODULE}.get_identity", _boom)

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved", "resolved_by": "fallback-human"},
        )

        assert r.status_code == 200
        assert approvals.approve.await_args.kwargs["approved_by"] == "fallback-human"

    async def test_no_identity_and_no_body_yields_none(self, client, container, monkeypatch):
        approvals = _setup_approvals(container)
        _patch_identity(monkeypatch, AuthIdentity())

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved"},  # no resolved_by at all
        )

        assert r.status_code == 200
        assert approvals.approve.await_args.kwargs["approved_by"] is None

    async def test_identity_present_but_actor_id_empty_falls_back_to_body(self, client, container, monkeypatch):
        """An identity whose ``actor_id`` is falsy (empty string) does NOT override
        the body — the guard is ``if actor_id:`` truthiness, not ``is not None``."""
        approvals = _setup_approvals(container)
        _patch_identity(
            monkeypatch,
            AuthIdentity(method=AuthMethod.ACTOR_HEADER, actor_id=""),
        )

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            json={"status": "approved", "resolved_by": "body-wins"},
        )

        assert r.status_code == 200
        assert approvals.approve.await_args.kwargs["approved_by"] == "body-wins"


# ---------------------------------------------------------------------------
# End-to-end through the REAL AuthMiddleware / get_identity (no monkeypatch):
# an X-EB-Actor-Id header resolves to an ACTOR_HEADER identity server-side and
# overrides the body's resolved_by.
# ---------------------------------------------------------------------------

class TestRealIdentityResolution:
    async def test_actor_header_identity_overrides_body(self, client, container):
        approvals = _setup_approvals(container)
        header_actor = str(uuid.uuid4())

        rid = uuid.uuid4()
        r = await client.patch(
            f"/guards/approvals/{rid}",
            headers={"X-EB-Actor-Id": header_actor},
            json={"status": "approved", "resolved_by": "client-forged"},
        )

        assert r.status_code == 200
        approvals.approve.assert_awaited_once()
        assert approvals.approve.await_args.kwargs["approved_by"] == header_actor
