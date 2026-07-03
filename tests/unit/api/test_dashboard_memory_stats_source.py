"""Unit tests for the NEW activity-source selection in ``GET /dashboard/memory/stats``.

Branch EB-FE adds truthful activity-source labelling to ``memory_stats`` (see
``memory-stats-1/5`` in ``elephantbroker/api/routes/dashboard.py``). The handler
now:

* prefers the DURABLE trace store (``container.trace_query_client`` — the
  ClickHouse-backed ``OtelTraceQueryClient``) whenever it is present AND reports
  ``available``, calling ``get_activity_stats(gateway_id=..., since=...)`` and
  emitting ``activity_source == "clickhouse"`` with a durable label/note and
  ``activity_window_capped is False``;
* falls back to the in-memory ``TraceLedger`` ONLY when the durable client is
  absent, reports itself unavailable, or raises — emitting
  ``activity_source == "ledger"`` with the in-memory label and (when the
  requested range exceeds buffer retention) a "capped" note.

These tests assert the additive dict keys ``activity_source`` /
``activity_source_label`` / ``note`` / ``activity_window_capped`` /
``activity_retention_seconds`` are TRUTHFUL for each path. All I/O is mocked
(graph adapter, trace query client, actor registry) via the shared
``tests/unit/api/conftest.py`` fixtures — no live infra, no network. Mocking
style mirrors ``test_routes_dashboard.py`` in the same directory.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# A syntactically valid actor UUID for the legacy X-EB-Actor-Id auth path
# (mirrors ADMIN_ACTOR in test_routes_dashboard.py).
ADMIN_ACTOR = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def admin_client(client, container):
    """The shared async client, authenticated as a high-authority (>=70) actor.

    Replicates the ``admin_client`` fixture from ``test_routes_dashboard.py`` so
    the READ-gated ``/dashboard/memory/stats`` route is reachable, and pre-caches
    bootstrap detection so no extra probe query desyncs graph call ordering.
    """
    container.actor_registry.resolve_actor = AsyncMock(
        return_value=SimpleNamespace(authority_level=100)
    )
    container._bootstrap_checked = True
    container._bootstrap_mode = False
    client.headers.update({"X-EB-Actor-Id": ADMIN_ACTOR})
    return client


def _durable_client(*, available=True, stats=None, error=None):
    """Build a stand-in for ``container.trace_query_client`` (OtelTraceQueryClient).

    ``get_activity_stats`` is an AsyncMock returning ``stats`` (or raising
    ``error``); ``available`` mirrors the client's readiness flag.
    """
    if error is not None:
        get_stats = AsyncMock(side_effect=error)
    else:
        get_stats = AsyncMock(return_value=stats)
    return SimpleNamespace(available=available, get_activity_stats=get_stats)


class TestActivitySourceClickHousePreferred:
    async def test_clickhouse_preferred_when_available(self, admin_client, container):
        """Durable client present + available → activity_source == 'clickhouse'.

        Asserts the durable label/note/capped metadata AND that the activity
        rates + sparkline are served from the durable payload (not the ledger).
        """
        bucket_ts = datetime(2026, 7, 2, 10, 0, 0, tzinfo=UTC)
        durable = {
            "extractions": 5,
            "dedups": 2,
            "supersessions": 1,
            "buckets": [{"timestamp": bucket_ts, "count": 5}],
        }
        container.trace_query_client = _durable_client(available=True, stats=durable)

        r = await admin_client.get("/dashboard/memory/stats?time_range=7d")
        assert r.status_code == 200
        body = r.json()

        # --- Source metadata is truthful for the durable path ---
        assert body["activity_source"] == "clickhouse"
        assert body["activity_source_label"] == "ClickHouse (durable)"
        # Durable store serves the full window — nothing is capped, retention N/A.
        assert body["activity_window_capped"] is False
        assert body["activity_retention_seconds"] is None
        assert "durable ClickHouse" in body["note"]

        # --- Activity numbers come from the durable payload ---
        assert body["extractions_in_period"] == 5
        assert body["dedup_rate"] == round(2 / 5, 4)
        assert body["supersession_rate"] == round(1 / 5, 4)
        assert len(body["creation_over_time"]) == 1
        assert body["creation_over_time"][0]["count"] == 5

        # --- The durable client was queried, gateway-scoped, over the window ---
        get_stats = container.trace_query_client.get_activity_stats
        get_stats.assert_awaited_once()
        kwargs = get_stats.await_args.kwargs
        assert "gateway_id" in kwargs and isinstance(kwargs["gateway_id"], str)
        assert "since" in kwargs and isinstance(kwargs["since"], datetime)

    async def test_clickhouse_never_touches_ledger(self, admin_client, container):
        """When durable serves the data, the in-memory ledger is not consulted."""
        container.trace_query_client = _durable_client(
            available=True,
            stats={"extractions": 0, "dedups": 0, "supersessions": 0, "buckets": []},
        )
        # Spy on the ledger's query_trace — it must NOT be awaited on the durable path.
        container.trace_ledger.query_trace = AsyncMock(return_value=[])

        r = await admin_client.get("/dashboard/memory/stats?time_range=24h")
        assert r.status_code == 200
        assert r.json()["activity_source"] == "clickhouse"
        container.trace_ledger.query_trace.assert_not_awaited()


class TestActivitySourceLedgerFallback:
    async def test_ledger_fallback_when_no_durable_client(self, admin_client, container):
        """No durable client (default None) → activity_source == 'ledger'.

        Uses a range (1h) within buffer retention so the fallback is NOT capped
        and carries no note.
        """
        assert container.trace_query_client is None  # conftest default

        r = await admin_client.get("/dashboard/memory/stats?time_range=1h")
        assert r.status_code == 200
        body = r.json()

        assert body["activity_source"] == "ledger"
        assert body["activity_source_label"] == "in-memory trace ledger"
        # Default TraceLedger retention is 3600s; 1h request == retention → not capped.
        assert body["activity_retention_seconds"] == 3600
        assert body["activity_window_capped"] is False
        # Note only appears when the window is capped — absent for an in-window range.
        assert "note" not in body

    async def test_ledger_fallback_capped_emits_note(self, admin_client, container):
        """A range wider than buffer retention → capped + explanatory ledger note."""
        assert container.trace_query_client is None

        r = await admin_client.get("/dashboard/memory/stats?time_range=7d")
        assert r.status_code == 200
        body = r.json()

        assert body["activity_source"] == "ledger"
        assert body["activity_source_label"] == "in-memory trace ledger"
        assert body["activity_retention_seconds"] == 3600
        # 7d (604800s) >> 3600s retention → the buffer cannot serve the range.
        assert body["activity_window_capped"] is True
        # The ledger note names the in-memory buffer and points at the durable store.
        assert "in-memory trace buffer" in body["note"]
        assert "1h" in body["note"]  # 3600s retention rendered as "1h"

    async def test_unavailable_durable_client_falls_back_without_query(
        self, admin_client, container
    ):
        """Durable client present but ``available=False`` → ledger, never queried."""
        client_stub = _durable_client(
            available=False,
            stats={"extractions": 9, "dedups": 9, "supersessions": 9, "buckets": []},
        )
        container.trace_query_client = client_stub

        r = await admin_client.get("/dashboard/memory/stats?time_range=1h")
        assert r.status_code == 200
        body = r.json()

        assert body["activity_source"] == "ledger"
        assert body["activity_source_label"] == "in-memory trace ledger"
        # An unavailable client must NOT be queried at all.
        client_stub.get_activity_stats.assert_not_awaited()
        # And its (would-be) numbers must not leak into the response.
        assert body["extractions_in_period"] == 0

    async def test_durable_error_falls_back_to_ledger(self, admin_client, container):
        """Durable client available but ``get_activity_stats`` raises → ledger path.

        The handler degrades gracefully (never 500s) and truthfully reports the
        source that actually served the data.
        """
        client_stub = _durable_client(
            available=True, error=RuntimeError("clickhouse down")
        )
        container.trace_query_client = client_stub

        r = await admin_client.get("/dashboard/memory/stats?time_range=1h")
        assert r.status_code == 200
        body = r.json()

        # It tried the durable store first...
        client_stub.get_activity_stats.assert_awaited_once()
        # ...then fell back to the ledger and said so.
        assert body["activity_source"] == "ledger"
        assert body["activity_source_label"] == "in-memory trace ledger"
        assert body["activity_retention_seconds"] == 3600

    async def test_durable_returns_none_falls_back_to_ledger(
        self, admin_client, container
    ):
        """Durable client available but returns ``None`` → ledger fallback.

        ``get_activity_stats`` yielding ``None`` leaves ``durable is None``, so
        the handler must fall through to the in-memory ledger rather than treat
        a null payload as a durable answer.
        """
        client_stub = _durable_client(available=True, stats=None)
        container.trace_query_client = client_stub

        r = await admin_client.get("/dashboard/memory/stats?time_range=1h")
        assert r.status_code == 200
        body = r.json()

        client_stub.get_activity_stats.assert_awaited_once()
        assert body["activity_source"] == "ledger"
        assert body["activity_source_label"] == "in-memory trace ledger"
