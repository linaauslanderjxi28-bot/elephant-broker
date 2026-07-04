"""In-memory append-only trace ledger with optional gateway auto-enrichment and OTEL log bridge."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from elephantbroker.runtime.interfaces.trace_ledger import ITraceLedger
from elephantbroker.schemas.trace import SessionListItem, SessionListResponse, TraceEvent, TraceQuery

if TYPE_CHECKING:
    from elephantbroker.schemas.config import TraceConfig


class TraceLedger(ITraceLedger):
    """Append-only in-memory event store with optional OTEL log export.

    Events are stored in insertion order and filtered on read.
    When ``gateway_id``, ``agent_key``, or ``agent_id`` are set,
    ``append_event()`` auto-enriches events missing those fields.

    Phase 9 additions:
    - Circular buffer eviction (memory_max_events + memory_ttl_seconds)
    - Optional OTEL LogRecord emission for durable persistence in ClickHouse
    """

    def __init__(
        self,
        gateway_id: str | None = None,
        otel_logger=None,
        config: TraceConfig | None = None,
        agent_key: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._events: list[TraceEvent] = []
        # consolidation-trace-1: O(1) lookup of an event by its own id. Kept in
        # lockstep with ``_events`` on every append and eviction so a single
        # event can be fetched by id (GET /trace/{id}) — the reference-scan in
        # ``get_evidence_chain`` never matches an event's OWN id, so without
        # this index every by-id fetch 404'd.
        self._events_by_id: dict[uuid.UUID, TraceEvent] = {}
        self._gateway_id = gateway_id
        self._agent_key = agent_key
        self._agent_id = agent_id
        self._otel_logger = otel_logger
        # Lazy import to avoid circular — config may be None for backward compat
        if config is not None:
            self._max_events = config.memory_max_events
            self._ttl_seconds = config.memory_ttl_seconds
        else:
            self._max_events = 10_000
            self._ttl_seconds = 3600

    def set_agent_identity(self, agent_key: str, agent_id: str) -> None:
        """Update agent identity (called after bootstrap resolves agent_key)."""
        self._agent_key = agent_key
        self._agent_id = agent_id

    async def append_event(self, event: TraceEvent) -> TraceEvent:
        if self._gateway_id and not event.gateway_id:
            event.gateway_id = self._gateway_id
        if self._agent_key and not event.agent_key:
            event.agent_key = self._agent_key
        if self._agent_id and not event.agent_id:
            event.agent_id = self._agent_id
        # OTEL correlation stamp — capture the active span's trace_id/span_id
        # onto the event (hex) BEFORE storing/emitting so in-memory reads and the
        # durable ClickHouse log row share the same ids as the Jaeger span. No-op
        # when no valid span context is active; never raises (see _stamp_span_context).
        self._stamp_span_context(event)
        self._events.append(event)
        self._events_by_id[event.id] = event
        self._evict_stale()

        # OTEL log export (durable persistence to ClickHouse via OTEL Collector)
        if self._otel_logger:
            try:
                self._emit_otel_log(event)
            except Exception:
                pass  # Never let OTEL export failure block trace recording

        return event

    def _evict_stale(self) -> None:
        """Enforce circular buffer (max events) and TTL-based eviction."""
        # Size cap — evict oldest when over limit. Drop the evicted event from
        # the by-id index too so a stale id can never resolve (or leak memory).
        while len(self._events) > self._max_events:
            evicted = self._events.pop(0)
            self._events_by_id.pop(evicted.id, None)
        # TTL cap — prune events older than ttl_seconds (lazy, on each append)
        if self._events and self._ttl_seconds > 0:
            cutoff = datetime.now(UTC) - timedelta(seconds=self._ttl_seconds)
            while self._events and self._events[0].timestamp < cutoff:
                evicted = self._events.pop(0)
                self._events_by_id.pop(evicted.id, None)

    def _stamp_span_context(self, event: TraceEvent) -> None:
        """Stamp the active OTEL span's trace_id/span_id onto the event (hex).

        Reads ``opentelemetry.trace.get_current_span().get_span_context()``; when
        the context is valid (non-zero trace_id), formats trace_id as 032x and
        span_id as 016x hex and sets them on the event. A missing/invalid span
        context (or a missing opentelemetry install) is a silent no-op — this
        must never raise or block trace recording, matching the try/except-swallow
        discipline in _emit_otel_log.
        """
        try:
            from opentelemetry.trace import get_current_span

            span_context = get_current_span().get_span_context()
            if span_context is None or not span_context.is_valid or span_context.trace_id == 0:
                return
            event.trace_id = format(span_context.trace_id, "032x")
            event.span_id = format(span_context.span_id, "016x")
        except Exception:
            pass  # Never let correlation stamping block trace recording

    def _emit_otel_log(self, event: TraceEvent) -> None:
        """Emit a TraceEvent as an OTEL LogRecord for durable storage."""
        try:
            try:
                from opentelemetry.sdk._logs import LogRecord
            except ImportError:
                # opentelemetry-sdk >= ~1.40 no longer re-exports LogRecord at the
                # package top level; it lives in the internal module. Fall back so
                # the durable export path (and its correlation ids) stays alive.
                from opentelemetry.sdk._logs._internal import LogRecord
            from opentelemetry.trace import StatusCode  # noqa: F401 — used for severity
        except ImportError:
            return

        # Correlation ids for the LogRecord (ints, as OTEL expects). Derived from
        # the hex stamped on the event by _stamp_span_context so the ClickHouse row
        # and the Jaeger span carry identical trace/span ids. trace_flags is read
        # live from the active span context (guarded; defaults to 0 = unsampled).
        record_trace_id: int | None = None
        record_span_id: int | None = None
        record_trace_flags = 0
        try:
            if event.trace_id and event.span_id:
                record_trace_id = int(event.trace_id, 16)
                record_span_id = int(event.span_id, 16)
                from opentelemetry.trace import get_current_span

                span_context = get_current_span().get_span_context()
                if span_context is not None and span_context.is_valid:
                    record_trace_flags = int(span_context.trace_flags)
        except Exception:
            record_trace_id = record_span_id = None  # correlation is best-effort

        self._otel_logger.emit(LogRecord(
            body=event.model_dump_json(),
            trace_id=record_trace_id,
            span_id=record_span_id,
            trace_flags=record_trace_flags,
            attributes={
                "event_type": event.event_type.value,
                "session_id": str(event.session_id) if event.session_id else "",
                "session_key": event.session_key or "",
                "gateway_id": event.gateway_id or "",
                "agent_key": event.agent_key or "",
            },
        ))

    async def query_trace(self, query: TraceQuery) -> list[TraceEvent]:
        results: list[TraceEvent] = []
        for ev in self._events:
            if query.event_types and ev.event_type not in query.event_types:
                continue
            if query.session_id and ev.session_id != query.session_id:
                continue
            if query.actor_ids and not set(ev.actor_ids).intersection(query.actor_ids):
                continue
            if query.from_timestamp and ev.timestamp < query.from_timestamp:
                continue
            if query.to_timestamp and ev.timestamp > query.to_timestamp:
                continue
            results.append(ev)

        if query.session_key is not None:
            results = [e for e in results if e.session_key == query.session_key]
        if query.gateway_id is not None:
            results = [e for e in results if e.gateway_id == query.gateway_id]

        # consolidation-trace-2: events are stored oldest→newest, so slicing the
        # front returned the OLDEST ``limit`` events and silently dropped the
        # most recent activity once the buffer exceeded ``limit``. Order
        # newest-first BEFORE applying offset/limit so pagination pages from the
        # most recent events. Callers that need chronological order (session
        # timeline / summary) re-sort by timestamp themselves.
        results.reverse()
        results = results[query.offset:]
        return results[: query.limit]

    async def list_sessions(
        self,
        gateway_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SessionListResponse:
        """Return unique sessions for a gateway, sorted by most recent activity.

        Scans in-memory events to find unique (session_id, session_key) pairs,
        computes first/last event timestamps and event counts, then applies
        pagination via offset/limit.
        """
        # Collect per-session stats keyed by session_id
        session_map: dict[uuid.UUID, dict] = {}
        for ev in self._events:
            if ev.session_id is None:
                continue
            if gateway_id is not None and ev.gateway_id != gateway_id:
                continue
            sid = ev.session_id
            if sid not in session_map:
                session_map[sid] = {
                    "session_id": sid,
                    "session_key": ev.session_key or "",
                    "first_event_at": ev.timestamp,
                    "last_event_at": ev.timestamp,
                    "event_count": 0,
                }
            entry = session_map[sid]
            entry["event_count"] += 1
            if ev.timestamp < entry["first_event_at"]:
                entry["first_event_at"] = ev.timestamp
            if ev.timestamp > entry["last_event_at"]:
                entry["last_event_at"] = ev.timestamp
            # Keep the most informative session_key (non-empty wins)
            if ev.session_key and not entry["session_key"]:
                entry["session_key"] = ev.session_key

        # Sort by most recent activity first
        sessions_sorted = sorted(
            session_map.values(),
            key=lambda s: s["last_event_at"],
            reverse=True,
        )
        total_count = len(sessions_sorted)
        page = sessions_sorted[offset: offset + limit]
        return SessionListResponse(
            sessions=[SessionListItem(**s) for s in page],
            total_count=total_count,
        )

    async def get_event_by_id(self, event_id: uuid.UUID) -> TraceEvent | None:
        """Return the single event with this id, or None.

        consolidation-trace-1: dedicated O(1) by-id primitive backing
        GET /trace/{id}. Gateway-agnostic — the route layer applies the
        gateway_id filter (a stale/cross-gateway id resolves to 404 there).
        """
        return self._events_by_id.get(event_id)

    async def get_evidence_chain(self, target_id: uuid.UUID) -> list[TraceEvent]:
        # consolidation-trace-1: GET /trace/{id} resolves a single event via
        # this method, but the reference scan below only matches events that
        # *reference* ``target_id`` (in their actor/artifact/claim/procedure/
        # goal id lists) — it never matches an event's OWN id, so every by-id
        # fetch 404'd. Seed the chain with the event whose id == target_id (if
        # any) so the root event is returned first. When ``target_id`` is a
        # fact/claim/actor id (the evidence-chain use case) no event carries
        # that id, so this is a safe no-op superset for existing callers.
        chain: list[TraceEvent] = []
        root = self._events_by_id.get(target_id)
        if root is not None:
            chain.append(root)
        for ev in self._events:
            if ev is root:
                continue
            refs = ev.actor_ids + ev.artifact_ids + ev.claim_ids + ev.procedure_ids + ev.goal_ids
            if target_id in refs:
                chain.append(ev)
        return chain
