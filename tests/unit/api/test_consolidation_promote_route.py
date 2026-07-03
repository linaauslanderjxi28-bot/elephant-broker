"""Unit tests for the NEW approve->promote path of the consolidation
``PATCH /consolidation/suggestions/{id}`` route (branch EB-FE, gap-5-4).

Covers the branch-new behavior of ``update_suggestion``:
  * approved -> fetches via ``store.get_suggestion``, calls
    ``promote_suggestion_to_procedure`` and returns the new ``procedure_id``,
    marking status ``approved`` ONLY after promotion succeeds;
  * rejected -> records status, returns ``procedure_id: None`` and never promotes;
  * unknown id -> 404;
  * promotion failure (returns None or raises) -> HTTP 500 leaving the
    suggestion pending (status never marked approved).

All I/O is mocked: the report store is an ``AsyncMock`` and the Cognee-backed
``promote_suggestion_to_procedure`` helper is patched at its source module (it is
imported lazily inside the route). Mirrors the mocking style of the sibling
``test_routes_consolidation.py``.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

PROMOTE_TARGET = (
    "elephantbroker.runtime.consolidation.stages.refine_procedures"
    ".promote_suggestion_to_procedure"
)

SUGGESTION_ID = "sugg-123"


@pytest.fixture
def mock_store():
    """An AsyncMock report store standing in for ConsolidationReportStore."""
    store = AsyncMock()
    store.get_suggestion = AsyncMock(
        return_value={
            "id": SUGGESTION_ID,
            "gateway_id": "local",
            "approval_status": "pending",
            "draft_procedure_json": '{"name": "draft"}',
        }
    )
    store.update_suggestion_status = AsyncMock(return_value=True)
    return store


def _url(suggestion_id: str = SUGGESTION_ID) -> str:
    return f"/consolidation/suggestions/{suggestion_id}"


async def test_approved_promotes_and_returns_procedure_id(container, client, mock_store):
    """approved -> promotes the draft and returns the new procedure_id (200)."""
    container.consolidation_report_store = mock_store
    proc = SimpleNamespace(id=uuid.uuid4())

    with patch(PROMOTE_TARGET, new=AsyncMock(return_value=proc)) as promote:
        resp = await client.patch(_url(), json={"approval_status": "approved"})

    assert resp.status_code == 200
    assert resp.json() == {
        "id": SUGGESTION_ID,
        "approval_status": "approved",
        "procedure_id": str(proc.id),
    }
    # The fetched suggestion is what gets promoted.
    promote.assert_awaited_once()
    assert promote.await_args.args[0] == mock_store.get_suggestion.return_value
    mock_store.get_suggestion.assert_awaited_once()
    assert mock_store.get_suggestion.await_args.args[0] == SUGGESTION_ID


async def test_approved_marks_status_only_after_promotion_succeeds(
    container, client, mock_store
):
    """Status is marked ``approved`` strictly AFTER promotion returns."""
    container.consolidation_report_store = mock_store
    proc = SimpleNamespace(id=uuid.uuid4())
    promote = AsyncMock(return_value=proc)

    # Attach both awaitables to one manager so we can assert call ordering:
    # promotion must happen before the status write.
    manager = Mock()
    manager.attach_mock(promote, "promote")
    manager.attach_mock(mock_store.update_suggestion_status, "update_status")

    with patch(PROMOTE_TARGET, new=promote):
        resp = await client.patch(_url(), json={"approval_status": "approved"})

    assert resp.status_code == 200
    ordered = [name for name, _, _ in manager.mock_calls]
    assert ordered == ["promote", "update_status"]
    mock_store.update_suggestion_status.assert_awaited_once_with(SUGGESTION_ID, "approved")


async def test_rejected_records_status_and_does_not_promote(container, client, mock_store):
    """rejected -> records status, returns procedure_id None, never promotes."""
    container.consolidation_report_store = mock_store

    with patch(PROMOTE_TARGET, new=AsyncMock()) as promote:
        resp = await client.patch(_url(), json={"approval_status": "rejected"})

    assert resp.status_code == 200
    assert resp.json() == {
        "id": SUGGESTION_ID,
        "approval_status": "rejected",
        "procedure_id": None,
    }
    promote.assert_not_awaited()
    mock_store.update_suggestion_status.assert_awaited_once_with(SUGGESTION_ID, "rejected")


async def test_unknown_suggestion_returns_404(container, client, mock_store):
    """Unknown id (store returns None) -> 404, no promotion, no status write."""
    mock_store.get_suggestion = AsyncMock(return_value=None)
    container.consolidation_report_store = mock_store

    with patch(PROMOTE_TARGET, new=AsyncMock()) as promote:
        resp = await client.patch(_url("missing"), json={"approval_status": "approved"})

    assert resp.status_code == 404
    promote.assert_not_awaited()
    mock_store.update_suggestion_status.assert_not_awaited()


async def test_promotion_returns_none_yields_500_and_leaves_pending(
    container, client, mock_store
):
    """Promotion returning None -> HTTP 500, suggestion left pending (no approve)."""
    container.consolidation_report_store = mock_store

    with patch(PROMOTE_TARGET, new=AsyncMock(return_value=None)):
        resp = await client.patch(_url(), json={"approval_status": "approved"})

    assert resp.status_code == 500
    # Never marked approved -> stays pending for operator retry.
    mock_store.update_suggestion_status.assert_not_awaited()


async def test_promotion_raising_yields_500_and_leaves_pending(
    container, client, mock_store
):
    """Promotion raising is swallowed -> HTTP 500, suggestion left pending."""
    container.consolidation_report_store = mock_store

    with patch(PROMOTE_TARGET, new=AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await client.patch(_url(), json={"approval_status": "approved"})

    assert resp.status_code == 500
    mock_store.update_suggestion_status.assert_not_awaited()


async def test_invalid_approval_status_returns_422_before_fetch(
    container, client, mock_store
):
    """Invalid approval_status -> 422 and the store is never consulted."""
    container.consolidation_report_store = mock_store

    with patch(PROMOTE_TARGET, new=AsyncMock()) as promote:
        resp = await client.patch(_url(), json={"approval_status": "maybe"})

    assert resp.status_code == 422
    promote.assert_not_awaited()
    mock_store.get_suggestion.assert_not_awaited()


async def test_no_report_store_returns_501(container, client):
    """No report store configured -> 501 (store guard runs first)."""
    # Ensure the attribute is absent/None.
    container.consolidation_report_store = None

    resp = await client.patch(_url(), json={"approval_status": "approved"})
    assert resp.status_code == 501
