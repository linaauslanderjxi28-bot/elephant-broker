"""OtelTraceQueryClient — queries ClickHouse for cross-session trace analytics.

Used by Stage 7 to detect repeated tool call sequences across sessions, and by
the ``/trace`` routes to read back durable TraceEvent history when
``EB_ENABLE_TRACE_LEDGER`` is disabled (see ``query_events`` / ``list_sessions`` /
``session_timeline`` / ``session_summary`` / ``get_event`` below). Reconstruction
parses each row's ``Body`` (written by ``TraceLedger._emit_otel_log`` as
``event.model_dump_json()``) back into a ``TraceEvent``.
Graceful degradation: returns empty results when ClickHouse not configured.

F6 (TODO-3-611): when ClickHouse is unavailable (missing dependency, failed
connection, query error) the client now emits ``DEGRADED_OPERATION`` trace
events and bumps the ``eb_degraded_operations_total`` counter so operators
can see *why* Stage 7 fell back to the SQLite-only path. Previously the
warning logs were the only signal and were silently lost on hosts that
shipped logs to a sink with no warning-level filter.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from elephantbroker.schemas.trace import (
    SessionListItem,
    TraceEvent,
    TraceEventType,
    TraceQuery,
)

if TYPE_CHECKING:
    from elephantbroker.runtime.interfaces.trace_ledger import ITraceLedger
    from elephantbroker.runtime.metrics import MetricsContext
    from elephantbroker.schemas.config import ClickHouseConfig

logger = logging.getLogger("elephantbroker.runtime.consolidation.otel_trace_query_client")

_COMPONENT = "clickhouse_trace_query"


class OtelTraceQueryClient:
    """Queries ClickHouse for cross-session trace analytics (AD-6)."""

    def __init__(
        self,
        config: ClickHouseConfig | None,
        trace_ledger: ITraceLedger | None = None,
        metrics: MetricsContext | None = None,
    ) -> None:
        self._client = None
        self._trace = trace_ledger
        self._metrics = metrics
        self._init_failure: tuple[str, str] | None = None  # (operation, reason) for lazy emit
        self._init_failure_emitted = False
        self._table = config.logs_table if config else "otel_logs"
        if config and config.enabled:
            try:
                import clickhouse_connect
                self._client = clickhouse_connect.get_client(
                    host=config.host,
                    port=config.port,
                    database=config.database,
                    username=config.user,
                    password=config.password,
                )
                logger.info("ClickHouse client connected (%s:%d/%s)", config.host, config.port, config.database)
            except ImportError:
                logger.warning("clickhouse-connect not installed — Stage 7 ClickHouse analytics unavailable")
                self._record_init_failure("connect_import", "clickhouse_connect_not_installed")
            except Exception as exc:
                logger.warning("ClickHouse connection failed", exc_info=True)
                self._record_init_failure("connect", f"connection_failed: {str(exc)[:120]}")

    def _record_init_failure(self, operation: str, reason: str) -> None:
        """Bump the degraded-op metric (sync) and stash the reason for the first async query.

        We can't emit a TraceEvent from __init__ because it's sync and the
        TraceLedger is async. Instead, the next ``get_tool_sequences()`` call
        emits a one-shot DEGRADED_OPERATION event so the trace shows the
        original failure context the first time something actually depended
        on this client.
        """
        self._init_failure = (operation, reason)
        if self._metrics is not None:
            try:
                self._metrics.inc_degraded_op(component=_COMPONENT, operation=operation)
            except Exception:
                pass

    async def _emit_init_failure_event(self, gateway_id: str) -> None:
        # F6 symmetry (Bucket F-R2, TODO-3-112): the query-failure payload at
        # ``get_tool_sequences`` below includes ``gateway_id`` so operators can
        # filter DEGRADED_OPERATION events by gateway. The init-failure payload
        # used to omit gateway_id, so a multi-gateway host whose ClickHouse
        # client failed to import couldn't tell *which* gateway first hit the
        # failure (the event fires on the first query after init). Threading
        # gateway_id from the caller restores symmetry between the two payloads.
        if self._init_failure is None or self._init_failure_emitted or self._trace is None:
            return
        operation, reason = self._init_failure
        self._init_failure_emitted = True
        try:
            await self._trace.append_event(TraceEvent(
                event_type=TraceEventType.DEGRADED_OPERATION,
                payload={
                    "component": _COMPONENT,
                    "operation": operation,
                    "reason": reason,
                    "gateway_id": gateway_id,
                },
            ))
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._client is not None

    async def get_tool_sequences(
        self,
        gateway_id: str,
        days: int = 7,
        min_sessions: int = 3,
    ) -> list[dict]:
        """Find tool call sequences from OTEL log records in ClickHouse.

        Queries the otel_logs table populated by OTEL Collector's clickhouse exporter.
        LogAttributes contain event_type and gateway_id from TraceLedger emission.
        Body contains the full TraceEvent JSON.
        """
        if not self._client:
            await self._emit_init_failure_event(gateway_id)
            return []

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        try:
            # ClickHouse SQL — parameterized query
            query = f"""
                SELECT
                    JSONExtractString(Body, 'session_key') AS session_key,
                    groupArray(JSONExtractString(Body, 'payload', 'tool_name')) AS tools
                FROM {self._table}
                WHERE LogAttributes['event_type'] = 'tool_invoked'
                  AND LogAttributes['gateway_id'] = %(gw)s
                  AND Timestamp >= %(cutoff)s
                GROUP BY session_key
                HAVING length(tools) >= 3
                ORDER BY length(tools) DESC
            """
            result = self._client.query(query, parameters={"gw": gateway_id, "cutoff": cutoff})
            rows = []
            for row in result.result_rows:
                session_key = row[0]
                tools = row[1] if isinstance(row[1], list) else json.loads(row[1])
                rows.append({"session_key": session_key, "tools": tools})
            return rows
        except Exception as exc:
            logger.warning("ClickHouse tool sequence query failed", exc_info=True)
            if self._metrics is not None:
                try:
                    self._metrics.inc_degraded_op(component=_COMPONENT, operation="query")
                except Exception:
                    pass
            if self._trace is not None:
                try:
                    await self._trace.append_event(TraceEvent(
                        event_type=TraceEventType.DEGRADED_OPERATION,
                        payload={
                            "component": _COMPONENT,
                            "operation": "query",
                            "reason": f"query_failed: {str(exc)[:120]}",
                            "gateway_id": gateway_id,
                        },
                    ))
                except Exception:
                    pass
            return []

    async def get_activity_stats(
        self,
        gateway_id: str,
        since: datetime,
    ) -> dict | None:
        """Gateway-scoped memory-activity aggregates + hourly creation series.

        Reads the durable ``otel_logs`` store (populated by the OTEL Collector's
        clickhouse exporter from ``TraceLedger`` LogRecords) so dashboard
        activity over wide ranges (6h/24h/7d) reflects real history instead of
        only the bounded in-memory trace buffer (memory-stats-1).

        Mirrors the in-memory ledger aggregation the dashboard falls back to:

        * ``extractions`` — sum of ``payload.facts_count`` (default 1) over
          ``fact_extracted`` events.
        * ``dedups`` — count of ``dedup_triggered`` events.
        * ``supersessions`` — count of ``fact_superseded`` events.
        * ``buckets`` — hourly ``fact_extracted`` totals (UTC) for the
          sparkline, oldest→newest to match the chronological x-axis.

        Returns ``None`` when the durable store is unavailable (not configured,
        connection lost, or query error) so the caller falls back to the
        in-memory ledger with an honest source label. Never raises.
        """
        if not self._client:
            await self._emit_init_failure_event(gateway_id)
            return None

        cutoff = since.isoformat()
        # ``facts_count`` defaults to 1 when absent OR 0 — identical semantics to
        # the ledger path's ``int(payload.get("facts_count", 1) or 1)``.
        _facts_count = "if(JSONExtractInt(Body, 'payload', 'facts_count') > 0, JSONExtractInt(Body, 'payload', 'facts_count'), 1)"

        try:
            agg = self._client.query(
                f"""
                SELECT
                    sumIf({_facts_count}, LogAttributes['event_type'] = 'fact_extracted') AS extractions,
                    countIf(LogAttributes['event_type'] = 'dedup_triggered') AS dedups,
                    countIf(LogAttributes['event_type'] = 'fact_superseded') AS supersessions
                FROM {self._table}
                WHERE LogAttributes['gateway_id'] = %(gw)s
                  AND Timestamp >= %(cutoff)s
                  AND LogAttributes['event_type'] IN ('fact_extracted', 'dedup_triggered', 'fact_superseded')
                """,
                parameters={"gw": gateway_id, "cutoff": cutoff},
            )
            extractions = dedups = supersessions = 0
            if agg.result_rows:
                row = agg.result_rows[0]
                extractions = int(row[0] or 0)
                dedups = int(row[1] or 0)
                supersessions = int(row[2] or 0)

            bres = self._client.query(
                f"""
                SELECT toStartOfHour(Timestamp, 'UTC') AS bucket, sum({_facts_count}) AS cnt
                FROM {self._table}
                WHERE LogAttributes['gateway_id'] = %(gw)s
                  AND Timestamp >= %(cutoff)s
                  AND LogAttributes['event_type'] = 'fact_extracted'
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                parameters={"gw": gateway_id, "cutoff": cutoff},
            )
            buckets: list[dict] = []
            for r in bres.result_rows:
                ts = r[0]
                if ts is None:
                    continue
                if isinstance(ts, datetime) and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                buckets.append({"timestamp": ts, "count": int(r[1] or 0)})

            return {
                "extractions": extractions,
                "dedups": dedups,
                "supersessions": supersessions,
                "buckets": buckets,
            }
        except Exception as exc:
            logger.warning("ClickHouse activity stats query failed", exc_info=True)
            if self._metrics is not None:
                try:
                    self._metrics.inc_degraded_op(component=_COMPONENT, operation="activity_stats_query")
                except Exception:
                    pass
            if self._trace is not None:
                try:
                    await self._trace.append_event(TraceEvent(
                        event_type=TraceEventType.DEGRADED_OPERATION,
                        payload={
                            "component": _COMPONENT,
                            "operation": "activity_stats_query",
                            "reason": f"query_failed: {str(exc)[:120]}",
                            "gateway_id": gateway_id,
                        },
                    ))
                except Exception:
                    pass
            return None

    # ------------------------------------------------------------------
    # /trace durable read-back (EB_ENABLE_TRACE_LEDGER=false read source)
    # ------------------------------------------------------------------
    #
    # These mirror the ``get_activity_stats`` query style: parameterized,
    # gateway-scoped (the ``gateway_id`` filter is MANDATORY on every query so a
    # tenant never reads another tenant's events), and guarded by ``self._client``
    # for graceful degradation (return empty / None, never raise). Each row's
    # ``Body`` is the ``event.model_dump_json()`` the ledger wrote in
    # ``_emit_otel_log``; reconstruction parses it straight back into a
    # ``TraceEvent``. Ordered newest-first (``Timestamp DESC``) to match the
    # in-memory ledger's ``query_trace`` pagination ordering.

    def _rows_to_events(self, rows) -> list[TraceEvent]:
        """Parse ``Body`` JSON from each row back into a ``TraceEvent``.

        Rows whose Body is empty or unparseable are skipped (defensive: a
        durable store may hold non-EB log records or a truncated Body) so one
        bad row never fails the whole read.
        """
        events: list[TraceEvent] = []
        for row in rows:
            body = row[0]
            if not body:
                continue
            try:
                events.append(TraceEvent.model_validate_json(body))
            except Exception:
                logger.debug("skipping unparseable otel_logs Body row", exc_info=True)
                continue
        return events

    @staticmethod
    def _coerce_utc(ts):
        """ClickHouse returns naive UTC datetimes — tag them so downstream
        Pydantic/serialization stays tz-aware (mirrors ``get_activity_stats``)."""
        if isinstance(ts, datetime) and ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts

    async def _emit_query_degraded(self, operation: str, exc: Exception, gateway_id: str) -> None:
        """Degraded-op signal for a read-back query failure (metric + trace event).

        Same shape as the inline emit in ``get_tool_sequences`` /
        ``get_activity_stats`` so operators see *which* read-back path degraded.
        """
        logger.warning("ClickHouse %s failed", operation, exc_info=True)
        if self._metrics is not None:
            try:
                self._metrics.inc_degraded_op(component=_COMPONENT, operation=operation)
            except Exception:
                pass
        if self._trace is not None:
            try:
                await self._trace.append_event(TraceEvent(
                    event_type=TraceEventType.DEGRADED_OPERATION,
                    payload={
                        "component": _COMPONENT,
                        "operation": operation,
                        "reason": f"query_failed: {str(exc)[:120]}",
                        "gateway_id": gateway_id,
                    },
                ))
            except Exception:
                pass

    async def query_events(self, query: TraceQuery) -> list[TraceEvent]:
        """Reconstruct full TraceEvents from ``otel_logs`` (newest-first).

        Filters by ``gateway_id`` (MANDATORY) plus any of session_id /
        session_key / event_types / from_timestamp / to_timestamp present on the
        query, honoring ``limit``/``offset``. ``actor_ids`` are not indexed in
        LogAttributes, so when present they are filtered in Python after
        reconstruction (rare on /trace; the dashboard filters by
        session/event_type/gateway). Returns ``[]`` when the durable store is
        unavailable; never raises.
        """
        gw = query.gateway_id or ""
        if not self._client:
            await self._emit_init_failure_event(gw)
            return []

        conditions = ["LogAttributes['gateway_id'] = %(gw)s"]
        params: dict = {"gw": gw, "limit": int(query.limit), "offset": int(query.offset)}
        if query.session_id is not None:
            conditions.append("LogAttributes['session_id'] = %(session_id)s")
            params["session_id"] = str(query.session_id)
        if query.session_key is not None:
            conditions.append("LogAttributes['session_key'] = %(session_key)s")
            params["session_key"] = query.session_key
        if query.event_types:
            conditions.append("LogAttributes['event_type'] IN %(event_types)s")
            params["event_types"] = [et.value for et in query.event_types]
        if query.from_timestamp is not None:
            conditions.append("Timestamp >= %(from_ts)s")
            params["from_ts"] = query.from_timestamp.isoformat()
        if query.to_timestamp is not None:
            conditions.append("Timestamp <= %(to_ts)s")
            params["to_ts"] = query.to_timestamp.isoformat()
        where = " AND ".join(conditions)

        try:
            result = self._client.query(
                f"""
                SELECT Body
                FROM {self._table}
                WHERE {where}
                ORDER BY Timestamp DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                parameters=params,
            )
            events = self._rows_to_events(result.result_rows)
            if query.actor_ids:
                wanted = set(query.actor_ids)
                events = [e for e in events if wanted.intersection(e.actor_ids)]
            return events
        except Exception as exc:
            await self._emit_query_degraded("query_events", exc, gw)
            return []

    async def get_event(self, gateway_id: str, event_id: uuid.UUID) -> TraceEvent | None:
        """Fetch a single reconstructed event by id (gateway-scoped).

        Backs ``GET /trace/{event_id}``. The gateway filter is in the SQL WHERE,
        so a cross-gateway id resolves to ``None`` (→ 404 at the route).
        """
        if not self._client:
            await self._emit_init_failure_event(gateway_id)
            return None
        try:
            result = self._client.query(
                f"""
                SELECT Body
                FROM {self._table}
                WHERE LogAttributes['gateway_id'] = %(gw)s
                  AND JSONExtractString(Body, 'id') = %(id)s
                ORDER BY Timestamp DESC
                LIMIT 1
                """,
                parameters={"gw": gateway_id, "id": str(event_id)},
            )
            events = self._rows_to_events(result.result_rows)
            return events[0] if events else None
        except Exception as exc:
            await self._emit_query_degraded("get_event_query", exc, gateway_id)
            return None

    async def session_timeline(
        self,
        gateway_id: str,
        session_id: uuid.UUID,
        limit: int = 10000,
    ) -> list[TraceEvent]:
        """All events for a session (gateway-scoped) — the route groups them
        into turns via ``group_events_by_turn``."""
        return await self.query_events(TraceQuery(
            session_id=session_id, gateway_id=gateway_id, limit=limit,
        ))

    async def session_summary(
        self,
        gateway_id: str,
        session_id: uuid.UUID,
        limit: int = 10000,
    ) -> list[TraceEvent]:
        """All events for a session (gateway-scoped) — the route aggregates them
        into a ``SessionSummary``."""
        return await self.query_events(TraceQuery(
            session_id=session_id, gateway_id=gateway_id, limit=limit,
        ))

    async def list_sessions(
        self,
        gateway_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SessionListItem]:
        """Group durable events into per-session entries, most-recent first.

        Groups by ``session_id`` (from LogAttributes) with first/last event
        timestamps and per-session counts — the durable analogue of the ledger's
        ``list_sessions`` scan. ``since`` optionally bounds the window. Returns
        ``[]`` when the durable store is unavailable; never raises.
        """
        if not self._client:
            await self._emit_init_failure_event(gateway_id)
            return []

        conditions = [
            "LogAttributes['gateway_id'] = %(gw)s",
            "LogAttributes['session_id'] != ''",
        ]
        params: dict = {"gw": gateway_id, "limit": int(limit)}
        if since is not None:
            conditions.append("Timestamp >= %(since)s")
            params["since"] = since.isoformat()
        where = " AND ".join(conditions)

        try:
            result = self._client.query(
                f"""
                SELECT
                    LogAttributes['session_id'] AS session_id,
                    max(LogAttributes['session_key']) AS session_key,
                    min(Timestamp) AS first_at,
                    max(Timestamp) AS last_at,
                    count() AS event_count
                FROM {self._table}
                WHERE {where}
                GROUP BY session_id
                ORDER BY last_at DESC
                LIMIT %(limit)s
                """,
                parameters=params,
            )
            items: list[SessionListItem] = []
            for row in result.result_rows:
                try:
                    sid = uuid.UUID(str(row[0]))
                except (ValueError, TypeError):
                    continue
                items.append(SessionListItem(
                    session_id=sid,
                    session_key=row[1] or "",
                    first_event_at=self._coerce_utc(row[2]),
                    last_event_at=self._coerce_utc(row[3]),
                    event_count=int(row[4] or 0),
                ))
            return items
        except Exception as exc:
            await self._emit_query_degraded("list_sessions_query", exc, gateway_id)
            return []

    def close(self) -> None:
        """Close ClickHouse connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
