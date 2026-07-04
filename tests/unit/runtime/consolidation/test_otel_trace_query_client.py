"""Tests for OtelTraceQueryClient."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from elephantbroker.runtime.consolidation.otel_trace_query_client import OtelTraceQueryClient
from elephantbroker.schemas.config import ClickHouseConfig
from elephantbroker.schemas.trace import TraceEvent, TraceEventType, TraceQuery


# ---------------------------------------------------------------------------
# Fake ClickHouse client for read-back tests
# ---------------------------------------------------------------------------
#
# Simulates the ``otel_logs`` table: holds a list of TraceEvents and answers
# ``.query(sql, parameters)`` by honoring the MANDATORY ``gw`` filter plus the
# handful of predicates the read-back SQL emits (session_id, event_types, id,
# GROUP BY). Rows echo the durable ``Body`` = ``event.model_dump_json()`` so the
# client's reconstruction path is exercised end-to-end.


class _FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCHClient:
    def __init__(self, events: list[TraceEvent]):
        self.events = events

    def query(self, sql, parameters=None):
        p = parameters or {}
        gw = p.get("gw")
        # gateway filter is MANDATORY — never leak another tenant's events.
        rows_src = [e for e in self.events if (e.gateway_id or "") == gw]

        if "GROUP BY session_id" in sql:
            groups: dict[str, dict] = {}
            for e in rows_src:
                if e.session_id is None:
                    continue
                key = str(e.session_id)
                g = groups.setdefault(
                    key,
                    {"skey": e.session_key or "", "first": e.timestamp, "last": e.timestamp, "cnt": 0},
                )
                g["cnt"] += 1
                g["first"] = min(g["first"], e.timestamp)
                g["last"] = max(g["last"], e.timestamp)
            rows = [[k, g["skey"], g["first"], g["last"], g["cnt"]] for k, g in groups.items()]
            rows.sort(key=lambda r: r[3], reverse=True)
            limit = p.get("limit")
            if limit is not None:
                rows = rows[:limit]
            return _FakeResult(rows)

        # SELECT Body (query_events / get_event)
        if "JSONExtractString(Body, 'id')" in sql:
            rows_src = [e for e in rows_src if str(e.id) == p.get("id")]
        if "session_id" in p:
            rows_src = [e for e in rows_src if str(e.session_id) == p["session_id"]]
        if "session_key" in p:
            rows_src = [e for e in rows_src if e.session_key == p["session_key"]]
        if "event_types" in p:
            ets = set(p["event_types"])
            rows_src = [e for e in rows_src if e.event_type.value in ets]
        rows_src.sort(key=lambda e: e.timestamp, reverse=True)
        offset = p.get("offset", 0)
        limit = p.get("limit")
        sliced = rows_src[offset:]
        if limit is not None:
            sliced = sliced[:limit]
        return _FakeResult([[e.model_dump_json()] for e in sliced])


def _mk_event(event_type, *, gw, sid=None, skey=None, ts=None, payload=None):
    return TraceEvent(
        event_type=event_type,
        session_id=sid,
        session_key=skey,
        gateway_id=gw,
        timestamp=ts or datetime.now(UTC),
        payload=payload or {},
    )


def _client_with(events):
    qc = OtelTraceQueryClient(ClickHouseConfig(enabled=False))
    qc._client = _FakeCHClient(events)
    return qc


class TestOtelTraceQueryClient:
    def test_not_available_when_disabled(self):
        config = ClickHouseConfig(enabled=False)
        client = OtelTraceQueryClient(config)
        assert client.available is False

    async def test_get_tool_sequences_returns_empty_when_unavailable(self):
        config = ClickHouseConfig(enabled=False)
        client = OtelTraceQueryClient(config)
        result = await client.get_tool_sequences("gw-1")
        assert result == []

    def test_close_no_error_when_no_client(self):
        config = ClickHouseConfig(enabled=False)
        client = OtelTraceQueryClient(config)
        client.close()  # Should not raise

    def test_available_property(self):
        config = ClickHouseConfig(enabled=False)
        client = OtelTraceQueryClient(config)
        assert client.available is False
        assert client._client is None


class TestOtelTraceQueryClientDegradedOps:
    """F6 (TODO-3-611): degraded-op wiring on ClickHouse failures."""

    def test_optional_constructor_args_default_to_none(self):
        """Backwards-compat: existing callers passing only the config still work."""
        client = OtelTraceQueryClient(ClickHouseConfig(enabled=False))
        assert client._trace is None
        assert client._metrics is None
        assert client._init_failure is None  # disabled config = no failure

    def test_constructor_accepts_trace_ledger_and_metrics(self):
        trace = MagicMock()
        metrics = MagicMock()
        client = OtelTraceQueryClient(
            ClickHouseConfig(enabled=False),
            trace_ledger=trace,
            metrics=metrics,
        )
        assert client._trace is trace
        assert client._metrics is metrics

    def test_import_failure_records_metric_and_stashes_reason(self, monkeypatch):
        """When clickhouse_connect import fails, metric fires + init_failure recorded."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "clickhouse_connect":
                raise ImportError("not installed in test env")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        metrics = MagicMock()
        client = OtelTraceQueryClient(
            ClickHouseConfig(enabled=True, host="x", port=9000, database="x"),
            metrics=metrics,
        )
        assert client._client is None
        metrics.inc_degraded_op.assert_called_once_with(
            component="clickhouse_trace_query",
            operation="connect_import",
        )
        assert client._init_failure is not None
        assert client._init_failure[0] == "connect_import"

    async def test_init_failure_emits_one_shot_trace_event_on_first_query(self, monkeypatch):
        """The deferred trace event fires once on the first async query call, not in __init__."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "clickhouse_connect":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        trace = MagicMock()
        trace.append_event = AsyncMock()
        client = OtelTraceQueryClient(
            ClickHouseConfig(enabled=True),
            trace_ledger=trace,
        )

        result1 = await client.get_tool_sequences("gw-1")
        result2 = await client.get_tool_sequences("gw-1")

        assert result1 == [] and result2 == []
        # Event should fire exactly once (one-shot semantics)
        assert trace.append_event.call_count == 1
        emitted_event = trace.append_event.call_args.args[0]
        assert emitted_event.event_type == TraceEventType.DEGRADED_OPERATION
        assert emitted_event.payload["component"] == "clickhouse_trace_query"
        assert emitted_event.payload["operation"] == "connect_import"
        # F6 symmetry (Bucket F-R2, TODO-3-112): init-failure payload must
        # carry gateway_id like the query-failure payload below. The test
        # passes "gw-1" to get_tool_sequences so that is what the event
        # should be stamped with.
        assert emitted_event.payload["gateway_id"] == "gw-1"

    async def test_query_failure_emits_event_and_metric(self):
        """A query exception fires both metric + degraded trace event each time."""
        config = ClickHouseConfig(enabled=False)  # avoids real connection
        trace = MagicMock()
        trace.append_event = AsyncMock()
        metrics = MagicMock()
        client = OtelTraceQueryClient(config, trace_ledger=trace, metrics=metrics)

        # Inject a fake client that raises on query
        fake_client = MagicMock()
        fake_client.query.side_effect = RuntimeError("BOOM")
        client._client = fake_client

        result = await client.get_tool_sequences("gw-1")

        assert result == []
        metrics.inc_degraded_op.assert_called_once_with(
            component="clickhouse_trace_query",
            operation="query",
        )
        trace.append_event.assert_called_once()
        emitted_event = trace.append_event.call_args.args[0]
        assert emitted_event.event_type == TraceEventType.DEGRADED_OPERATION
        assert emitted_event.payload["operation"] == "query"
        assert emitted_event.payload["gateway_id"] == "gw-1"


class TestReadBackReconstruction:
    """/trace durable read-back: reconstruct TraceEvents from otel_logs Body."""

    async def test_query_events_reconstructs_and_gateway_filters(self):
        sid = uuid.uuid4()
        events = [
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="gw-a", sid=sid,
                      skey="agent:main:main", payload={"facts_count": 2}),
            _mk_event(TraceEventType.RETRIEVAL_PERFORMED, gw="gw-a", sid=sid),
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="gw-b", sid=sid),  # other tenant
        ]
        qc = _client_with(events)
        out = await qc.query_events(TraceQuery(gateway_id="gw-a"))
        assert len(out) == 2  # never the gw-b event
        assert all(isinstance(e, TraceEvent) for e in out)
        assert all(e.gateway_id == "gw-a" for e in out)
        # payload / event_type / session_key preserved through the round trip
        fe = [e for e in out if e.event_type == TraceEventType.FACT_EXTRACTED][0]
        assert fe.payload["facts_count"] == 2
        assert fe.session_key == "agent:main:main"

    async def test_query_events_newest_first(self):
        now = datetime.now(UTC)
        events = [
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", ts=now - timedelta(seconds=10)),
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", ts=now),
        ]
        qc = _client_with(events)
        out = await qc.query_events(TraceQuery(gateway_id="g"))
        assert out[0].timestamp > out[1].timestamp  # newest-first, matches ledger

    async def test_query_events_event_type_filter(self):
        events = [
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g"),
            _mk_event(TraceEventType.GUARD_TRIGGERED, gw="g"),
        ]
        qc = _client_with(events)
        out = await qc.query_events(
            TraceQuery(gateway_id="g", event_types=[TraceEventType.GUARD_TRIGGERED])
        )
        assert len(out) == 1
        assert out[0].event_type == TraceEventType.GUARD_TRIGGERED

    async def test_get_event_by_id_gateway_scoped(self):
        target = _mk_event(TraceEventType.FACT_EXTRACTED, gw="g")
        other = _mk_event(TraceEventType.FACT_EXTRACTED, gw="other")
        qc = _client_with([target, other])
        got = await qc.get_event(gateway_id="g", event_id=target.id)
        assert got is not None and got.id == target.id
        # cross-gateway id resolves to None (→ 404 at the route)
        assert await qc.get_event(gateway_id="g", event_id=other.id) is None

    async def test_list_sessions_groups_and_filters(self):
        now = datetime.now(UTC)
        s1, s2 = uuid.uuid4(), uuid.uuid4()
        events = [
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", sid=s1, skey="k1", ts=now),
            _mk_event(TraceEventType.RETRIEVAL_PERFORMED, gw="g", sid=s1, skey="k1",
                      ts=now + timedelta(seconds=5)),
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", sid=s2, skey="k2",
                      ts=now + timedelta(seconds=10)),
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="other", sid=uuid.uuid4(), skey="x"),
        ]
        qc = _client_with(events)
        out = await qc.list_sessions(gateway_id="g")
        assert len(out) == 2  # only gw=g sessions
        by_id = {i.session_id: i for i in out}
        assert by_id[s1].event_count == 2
        assert by_id[s2].event_count == 1
        # most-recent session first
        assert out[0].session_id == s2

    async def test_session_timeline_delegates_to_query_events(self):
        sid = uuid.uuid4()
        events = [
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", sid=sid),
            _mk_event(TraceEventType.FACT_EXTRACTED, gw="g", sid=uuid.uuid4()),
        ]
        qc = _client_with(events)
        out = await qc.session_timeline(gateway_id="g", session_id=sid)
        assert len(out) == 1
        assert out[0].session_id == sid

    async def test_read_back_returns_empty_when_unavailable(self):
        qc = OtelTraceQueryClient(ClickHouseConfig(enabled=False))  # no _client
        assert qc.available is False
        assert await qc.query_events(TraceQuery(gateway_id="g")) == []
        assert await qc.list_sessions(gateway_id="g") == []
        assert await qc.get_event(gateway_id="g", event_id=uuid.uuid4()) is None

    async def test_query_events_degrades_on_error(self):
        qc = OtelTraceQueryClient(ClickHouseConfig(enabled=False))
        trace = MagicMock()
        trace.append_event = AsyncMock()
        metrics = MagicMock()
        qc._trace = trace
        qc._metrics = metrics
        boom = MagicMock()
        boom.query.side_effect = RuntimeError("BOOM")
        qc._client = boom
        assert await qc.query_events(TraceQuery(gateway_id="g")) == []
        metrics.inc_degraded_op.assert_called_once_with(
            component="clickhouse_trace_query", operation="query_events"
        )
        trace.append_event.assert_called_once()
