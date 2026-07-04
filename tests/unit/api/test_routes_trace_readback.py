"""Tests for /trace durable read-back gated by EB_ENABLE_TRACE_LEDGER.

Spec: EB_ENABLE_TRACE_LEDGER is a /trace READ-SOURCE selector.
- default TRUE  → /trace reads the in-memory TraceLedger (historical behavior)
- FALSE         → /trace reads durable history from ClickHouse via
                  OtelTraceQueryClient; if ClickHouse is unavailable it falls
                  back to the in-memory ledger (never errors).

The flag only changes where /trace READS — it never gates the ledger's
write/export path. Each response carries an honest ``X-EB-Trace-Source`` header.

These tests drive the real OtelTraceQueryClient with a fake ClickHouse client
returning synthetic otel_logs rows (Body = a TraceEvent JSON across two
gateways) so reconstruction + gateway isolation are exercised end-to-end.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from elephantbroker.runtime.consolidation.otel_trace_query_client import OtelTraceQueryClient
from elephantbroker.schemas.config import ClickHouseConfig, ElephantBrokerConfig
from elephantbroker.schemas.trace import TraceEvent, TraceEventType


# ---------------------------------------------------------------------------
# Fake ClickHouse client (otel_logs simulator) — honors the MANDATORY gw filter
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCHClient:
    def __init__(self, events: list[TraceEvent]):
        self.events = events

    def query(self, sql, parameters=None):
        p = parameters or {}
        gw = p.get("gw")
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

        if "JSONExtractString(Body, 'id')" in sql:
            rows_src = [e for e in rows_src if str(e.id) == p.get("id")]
        if "session_id" in p:
            rows_src = [e for e in rows_src if str(e.session_id) == p["session_id"]]
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


def _ev(event_type, *, gw="", sid=None, skey=None, ts=None, payload=None):
    return TraceEvent(
        event_type=event_type,
        session_id=sid,
        session_key=skey,
        gateway_id=gw,
        timestamp=ts or datetime.now(UTC),
        payload=payload or {},
    )


def _wire_clickhouse(container, events, *, enabled=False):
    """Point the container at a ClickHouse read-back client and set the flag.

    ``enabled`` is EB_ENABLE_TRACE_LEDGER. When False the routes should read
    from ClickHouse; when True they must ignore the (available) client and read
    the ledger.
    """
    qc = OtelTraceQueryClient(ClickHouseConfig(enabled=False))
    qc._client = _FakeCHClient(events)  # available == True
    assert qc.available is True
    container.trace_query_client = qc
    container.config = ElephantBrokerConfig(enable_trace_ledger=enabled)
    return qc


# ---------------------------------------------------------------------------
# flag=false → read from ClickHouse, reconstruct, gateway-filtered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestReadBackFromClickHouse:
    async def test_list_traces_reads_clickhouse(self, client, container):
        sid = uuid.uuid4()
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid, skey="k",
                payload={"facts_count": 3}),
            _ev(TraceEventType.FACT_EXTRACTED, gw="other-gw", sid=sid),  # other tenant
        ])
        r = await client.get("/trace/")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        data = r.json()
        # only the gw="" event is returned — never the other tenant's
        assert len(data) == 1
        assert data[0]["event_type"] == "fact_extracted"
        assert data[0]["session_key"] == "k"
        assert data[0]["payload"]["facts_count"] == 3

    async def test_query_traces_event_type_filter(self, client, container):
        sid = uuid.uuid4()
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid),
            _ev(TraceEventType.GUARD_TRIGGERED, gw="", sid=sid),
            _ev(TraceEventType.FACT_EXTRACTED, gw="other-gw", sid=sid),
        ])
        r = await client.post("/trace/query", json={"event_types": ["fact_extracted"]})
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        data = r.json()
        assert len(data) == 1
        assert data[0]["event_type"] == "fact_extracted"

    async def test_query_traces_gateway_override_never_leaks(self, client, container):
        """Even if the body asks for another gateway, the middleware gw wins."""
        sid = uuid.uuid4()
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid),
            _ev(TraceEventType.FACT_EXTRACTED, gw="other-gw", sid=sid),
        ])
        r = await client.post("/trace/query", json={"gateway_id": "other-gw"})
        assert r.status_code == 200
        data = r.json()
        # overridden to "" → only the "" event
        assert len(data) == 1
        assert data[0]["gateway_id"] == ""

    async def test_session_timeline_reads_clickhouse(self, client, container):
        sid = uuid.uuid4()
        base = datetime.now(UTC)
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid, ts=base),
            _ev(TraceEventType.AFTER_TURN_COMPLETED, gw="", sid=sid, ts=base + timedelta(seconds=1)),
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid, ts=base + timedelta(seconds=2)),
        ])
        r = await client.get(f"/trace/session/{sid}/timeline")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        groups = r.json()
        assert len(groups) == 2  # split at AFTER_TURN_COMPLETED

    async def test_session_summary_reads_clickhouse(self, client, container):
        sid = uuid.uuid4()
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid),
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid),
            _ev(TraceEventType.RETRIEVAL_PERFORMED, gw="", sid=sid),
        ])
        r = await client.get(f"/trace/session/{sid}/summary")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        s = r.json()
        assert s["total_events"] == 3
        assert s["facts_extracted"] == 2
        assert s["retrieval_count"] == 1

    async def test_list_sessions_reads_clickhouse_gateway_isolated(self, client, container):
        s_local, s_other = uuid.uuid4(), uuid.uuid4()
        _wire_clickhouse(container, [
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=s_local, skey="k1"),
            _ev(TraceEventType.FACT_EXTRACTED, gw="other-gw", sid=s_other, skey="k2"),
        ])
        r = await client.get("/trace/sessions")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        data = r.json()
        assert data["total_count"] == 1
        assert data["sessions"][0]["session_id"] == str(s_local)

    async def test_get_event_reads_clickhouse(self, client, container):
        target = _ev(TraceEventType.FACT_EXTRACTED, gw="")
        other = _ev(TraceEventType.FACT_EXTRACTED, gw="other-gw")
        _wire_clickhouse(container, [target, other])
        r = await client.get(f"/trace/{target.id}")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "clickhouse"
        assert r.json()["id"] == str(target.id)
        # a cross-gateway event id resolves to 404
        r2 = await client.get(f"/trace/{other.id}")
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# flag=false but ClickHouse unavailable → fall back to the in-memory ledger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestFallbackToLedger:
    async def test_flag_false_but_client_none_uses_ledger(self, client, container):
        container.trace_ledger._events.clear()
        sid = uuid.uuid4()
        await container.trace_ledger.append_event(
            _ev(TraceEventType.FACT_EXTRACTED, gw="", sid=sid, skey="from-ledger")
        )
        # flag false, but no ClickHouse client available
        container.trace_query_client = None
        container.config = ElephantBrokerConfig(enable_trace_ledger=False)

        r = await client.get("/trace/")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "ledger"
        data = r.json()
        assert len(data) == 1
        assert data[0]["session_key"] == "from-ledger"

    async def test_flag_false_client_unavailable_uses_ledger(self, client, container):
        container.trace_ledger._events.clear()
        await container.trace_ledger.append_event(
            _ev(TraceEventType.FACT_EXTRACTED, gw="", skey="ledger-2")
        )
        # A client that reports itself unavailable must not be used.
        qc = OtelTraceQueryClient(ClickHouseConfig(enabled=False))
        assert qc.available is False
        container.trace_query_client = qc
        container.config = ElephantBrokerConfig(enable_trace_ledger=False)

        r = await client.get("/trace/")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "ledger"


# ---------------------------------------------------------------------------
# flag=true → always the in-memory ledger, even when ClickHouse is available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestFlagTrueUsesLedger:
    async def test_flag_true_ignores_available_clickhouse(self, client, container):
        container.trace_ledger._events.clear()
        await container.trace_ledger.append_event(
            _ev(TraceEventType.FACT_EXTRACTED, gw="", skey="from-ledger")
        )
        # ClickHouse is available AND holds a distinct event, but the flag is True
        # so the ledger must win.
        _wire_clickhouse(
            container,
            [_ev(TraceEventType.GUARD_TRIGGERED, gw="", skey="from-clickhouse")],
            enabled=True,
        )
        r = await client.get("/trace/")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "ledger"
        data = r.json()
        assert len(data) == 1
        assert data[0]["session_key"] == "from-ledger"
        assert data[0]["event_type"] == "fact_extracted"

    async def test_default_config_none_uses_ledger(self, client, container):
        """Default (no config wired) behaves as flag=True → ledger."""
        container.trace_ledger._events.clear()
        await container.trace_ledger.append_event(
            _ev(TraceEventType.FACT_EXTRACTED, gw="", skey="default")
        )
        # container.config stays None (as the fixture leaves it); a CH client is
        # present but must be ignored because the flag defaults to True.
        _wire_clickhouse(
            container,
            [_ev(TraceEventType.GUARD_TRIGGERED, gw="")],
            enabled=True,
        )
        container.config = None  # explicit: default path
        r = await client.get("/trace/")
        assert r.status_code == 200
        assert r.headers["X-EB-Trace-Source"] == "ledger"
        assert r.json()[0]["session_key"] == "default"
