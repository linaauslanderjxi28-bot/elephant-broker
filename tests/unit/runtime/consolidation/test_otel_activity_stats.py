"""Unit tests for OtelTraceQueryClient.get_activity_stats (branch EB-FE, memory-stats-1).

Covers the NEW gateway-scoped ClickHouse activity-stats query over ``otel_logs``:
extractions (sum of payload.facts_count, default 1), dedups, supersessions, and
hourly UTC creation buckets; naive datetime normalization; and the "never
raises — returns None on any client error" contract.

All I/O is mocked: no real ClickHouse client, no network. Mirrors the mocking
style of the sibling ``test_otel_trace_query_client.py`` (disabled config +
injected fake ``_client`` whose ``.query()`` returns objects with
``.result_rows``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from elephantbroker.runtime.consolidation.otel_trace_query_client import OtelTraceQueryClient
from elephantbroker.schemas.config import ClickHouseConfig
from elephantbroker.schemas.trace import TraceEventType

SINCE = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)


def _result(rows):
    """A stand-in for a clickhouse_connect QueryResult: only ``.result_rows`` is read."""
    r = MagicMock()
    r.result_rows = rows
    return r


def _client_with_disabled_config(**kwargs):
    """Build a client whose real connection is skipped (enabled=False)."""
    return OtelTraceQueryClient(ClickHouseConfig(enabled=False), **kwargs)


class TestActivityStatsUnavailable:
    async def test_returns_none_when_no_client(self):
        client = _client_with_disabled_config()
        assert client._client is None
        result = await client.get_activity_stats("gw-1", SINCE)
        assert result is None

    async def test_unavailable_emits_one_shot_init_failure_event(self, monkeypatch):
        """When ClickHouse never connected, the first activity-stats call emits the
        deferred init-failure DEGRADED_OPERATION event stamped with gateway_id."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "clickhouse_connect":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        trace = MagicMock()
        trace.append_event = AsyncMock()
        client = OtelTraceQueryClient(ClickHouseConfig(enabled=True), trace_ledger=trace)

        result = await client.get_activity_stats("gw-42", SINCE)

        assert result is None
        trace.append_event.assert_called_once()
        event = trace.append_event.call_args.args[0]
        assert event.event_type == TraceEventType.DEGRADED_OPERATION
        assert event.payload["operation"] == "connect_import"
        assert event.payload["gateway_id"] == "gw-42"


class TestActivityStatsHappyPath:
    async def test_aggregates_and_buckets(self):
        client = _client_with_disabled_config()
        agg = _result([[5, 2, 1]])
        buckets = _result([
            [datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC), 3],
            [datetime(2026, 7, 1, 11, 0, 0, tzinfo=UTC), 2],
        ])
        fake = MagicMock()
        fake.query.side_effect = [agg, buckets]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result == {
            "extractions": 5,
            "dedups": 2,
            "supersessions": 1,
            "buckets": [
                {"timestamp": datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC), "count": 3},
                {"timestamp": datetime(2026, 7, 1, 11, 0, 0, tzinfo=UTC), "count": 2},
            ],
        }

    async def test_buckets_preserve_query_order(self):
        """Buckets are returned in the order ClickHouse yields them (ORDER BY bucket ASC)."""
        client = _client_with_disabled_config()
        ordered = [
            [datetime(2026, 7, 1, 8, 0, 0, tzinfo=UTC), 1],
            [datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC), 4],
            [datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC), 2],
        ]
        fake = MagicMock()
        fake.query.side_effect = [_result([[7, 0, 0]]), _result(ordered)]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert [b["timestamp"].hour for b in result["buckets"]] == [8, 9, 10]
        assert [b["count"] for b in result["buckets"]] == [1, 4, 2]

    async def test_empty_results_zero_and_empty_buckets(self):
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([]), _result([])]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result == {
            "extractions": 0,
            "dedups": 0,
            "supersessions": 0,
            "buckets": [],
        }

    async def test_none_aggregate_values_coerced_to_zero(self):
        """ClickHouse sumIf/countIf can yield NULL — coerced to 0, not raising."""
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[None, None, None]]), _result([])]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result["extractions"] == 0
        assert result["dedups"] == 0
        assert result["supersessions"] == 0


class TestActivityStatsBucketTimestamps:
    async def test_naive_bucket_timestamp_normalized_to_utc(self):
        naive = datetime(2026, 7, 1, 12, 0, 0)  # tzinfo=None
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[0, 0, 0]]), _result([[naive, 9]])]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert len(result["buckets"]) == 1
        ts = result["buckets"][0]["timestamp"]
        assert ts.tzinfo is UTC
        assert ts == datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)

    async def test_tzaware_bucket_timestamp_preserved(self):
        aware = datetime(2026, 7, 1, 13, 0, 0, tzinfo=UTC)
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[0, 0, 0]]), _result([[aware, 4]])]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result["buckets"][0]["timestamp"] == aware

    async def test_none_bucket_timestamp_skipped(self):
        client = _client_with_disabled_config()
        rows = [
            [None, 3],
            [datetime(2026, 7, 1, 14, 0, 0, tzinfo=UTC), 7],
        ]
        fake = MagicMock()
        fake.query.side_effect = [_result([[10, 0, 0]]), _result(rows)]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert len(result["buckets"]) == 1
        assert result["buckets"][0]["count"] == 7

    async def test_none_bucket_count_coerced_to_zero(self):
        client = _client_with_disabled_config()
        ts = datetime(2026, 7, 1, 15, 0, 0, tzinfo=UTC)
        fake = MagicMock()
        fake.query.side_effect = [_result([[0, 0, 0]]), _result([[ts, None]])]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result["buckets"][0]["count"] == 0


class TestActivityStatsQuerySemantics:
    async def test_gateway_and_cutoff_bound_as_parameters(self):
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[1, 0, 0]]), _result([])]
        client._client = fake

        await client.get_activity_stats("gw-xyz", SINCE)

        # Both the aggregate and the bucket query are parameterized on gw + cutoff.
        assert fake.query.call_count == 2
        for call in fake.query.call_args_list:
            params = call.kwargs["parameters"]
            assert params["gw"] == "gw-xyz"
            assert params["cutoff"] == SINCE.isoformat()

    async def test_naive_since_normalized_via_isoformat(self):
        """A naive ``since`` flows through ``.isoformat()`` unchanged (no crash)."""
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[1, 0, 0]]), _result([])]
        client._client = fake
        naive_since = datetime(2026, 6, 30, 0, 0, 0)

        await client.get_activity_stats("gw-1", naive_since)

        params = fake.query.call_args_list[0].kwargs["parameters"]
        assert params["cutoff"] == naive_since.isoformat()

    async def test_facts_count_default_and_hourly_utc_in_sql(self):
        """The NEW default-1 facts_count semantics and hourly UTC bucketing live in
        the SQL text: extractions sum over fact_extracted, hourly toStartOfHour UTC."""
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[1, 0, 0]]), _result([])]
        client._client = fake

        await client.get_activity_stats("gw-1", SINCE)

        agg_sql = fake.query.call_args_list[0].args[0]
        bucket_sql = fake.query.call_args_list[1].args[0]
        # default-1 when facts_count absent/zero
        assert "facts_count" in agg_sql
        assert "> 0" in agg_sql and ", 1)" in agg_sql
        # only the three activity event types feed the aggregate
        assert "fact_extracted" in agg_sql
        assert "dedup_triggered" in agg_sql
        assert "fact_superseded" in agg_sql
        # hourly UTC bucketing for the sparkline
        assert "toStartOfHour(Timestamp, 'UTC')" in bucket_sql
        assert "ORDER BY bucket ASC" in bucket_sql


class TestActivityStatsErrorHandling:
    async def test_query_error_returns_none_and_emits_metric_and_event(self):
        config = ClickHouseConfig(enabled=False)
        trace = MagicMock()
        trace.append_event = AsyncMock()
        metrics = MagicMock()
        client = OtelTraceQueryClient(config, trace_ledger=trace, metrics=metrics)

        fake = MagicMock()
        fake.query.side_effect = RuntimeError("BOOM")
        client._client = fake

        result = await client.get_activity_stats("gw-err", SINCE)

        assert result is None
        metrics.inc_degraded_op.assert_called_once_with(
            component="clickhouse_trace_query",
            operation="activity_stats_query",
        )
        trace.append_event.assert_called_once()
        event = trace.append_event.call_args.args[0]
        assert event.event_type == TraceEventType.DEGRADED_OPERATION
        assert event.payload["operation"] == "activity_stats_query"
        assert event.payload["gateway_id"] == "gw-err"
        assert event.payload["reason"].startswith("query_failed:")

    async def test_bucket_query_error_returns_none(self):
        """Failure on the *second* (bucket) query is still swallowed -> None."""
        client = _client_with_disabled_config()
        fake = MagicMock()
        fake.query.side_effect = [_result([[3, 1, 0]]), RuntimeError("bucket boom")]
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result is None

    async def test_never_raises_when_trace_and_metrics_absent(self):
        """No trace/metrics wired: a query error must still not propagate."""
        client = _client_with_disabled_config()
        assert client._trace is None
        assert client._metrics is None
        fake = MagicMock()
        fake.query.side_effect = RuntimeError("BOOM")
        client._client = fake

        result = await client.get_activity_stats("gw-1", SINCE)

        assert result is None
