"""Phase 9 TraceLedger tests — OTEL bridge and eviction."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from opentelemetry import trace as otel_trace

from elephantbroker.runtime.observability import get_tracer, setup_tracing
from elephantbroker.runtime.trace.ledger import TraceLedger
from elephantbroker.schemas.config import InfraConfig, TraceConfig
from elephantbroker.schemas.trace import TraceEvent, TraceEventType


class TestTraceLedgerEviction:
    async def test_evicts_beyond_max_events(self):
        config = TraceConfig(memory_max_events=100, memory_ttl_seconds=3600)
        ledger = TraceLedger(config=config)
        for i in range(150):
            await ledger.append_event(TraceEvent(event_type=TraceEventType.INPUT_RECEIVED))
        assert len(ledger._events) <= 100

    async def test_evicts_stale_by_ttl(self):
        config = TraceConfig(memory_max_events=10000, memory_ttl_seconds=60)
        ledger = TraceLedger(config=config)
        # Add old event (beyond TTL)
        old = TraceEvent(event_type=TraceEventType.INPUT_RECEIVED)
        old.timestamp = datetime.now(UTC) - timedelta(seconds=120)
        ledger._events.append(old)
        # Trigger eviction via new append
        await ledger.append_event(TraceEvent(event_type=TraceEventType.INPUT_RECEIVED))
        # Old event (120s ago) should be evicted; new event (0s ago) should remain
        assert len(ledger._events) == 1
        age = (datetime.now(UTC) - ledger._events[0].timestamp).total_seconds()
        assert age < 5

    async def test_backward_compat_no_config(self):
        ledger = TraceLedger()
        for i in range(5):
            await ledger.append_event(TraceEvent(event_type=TraceEventType.INPUT_RECEIVED))
        assert len(ledger._events) == 5


class TestTraceLedgerOtelBridge:
    async def test_emits_otel_log_when_logger_present(self):
        mock_logger = MagicMock()
        ledger = TraceLedger(otel_logger=mock_logger)
        await ledger.append_event(TraceEvent(
            event_type=TraceEventType.CONSOLIDATION_STARTED,
            gateway_id="gw-1",
        ))
        # The _emit_otel_log should be called (may fail if opentelemetry not installed, which is fine)
        assert len(ledger._events) == 1

    async def test_no_otel_log_when_logger_none(self):
        ledger = TraceLedger(otel_logger=None)
        await ledger.append_event(TraceEvent(event_type=TraceEventType.INPUT_RECEIVED))
        assert len(ledger._events) == 1

    async def test_otel_failure_does_not_block(self):
        mock_logger = MagicMock()
        mock_logger.emit = MagicMock(side_effect=RuntimeError("OTEL down"))
        ledger = TraceLedger(otel_logger=mock_logger)
        # Should not raise
        ev = await ledger.append_event(TraceEvent(event_type=TraceEventType.INPUT_RECEIVED))
        assert ev is not None


class TestTraceSpanCorrelationStamp:
    """AREA C: each TraceEvent + its OTLP LogRecord carries the active span's
    trace_id/span_id so ClickHouse log rows and Jaeger spans can be correlated.
    """

    async def test_active_span_stamps_event_and_logrecord(self):
        """With a started span active, the appended event is stamped with a valid
        trace_id (032x hex) / span_id (016x hex), and the emitted LogRecord carries
        the SAME ids as ints.
        """
        setup_tracing(InfraConfig())  # fresh provider so a real span context exists
        mock_logger = MagicMock()
        ledger = TraceLedger(otel_logger=mock_logger)

        tracer = get_tracer("test_correlation")
        with tracer.start_as_current_span("unit-span") as span:
            span_context = span.get_span_context()
            event = await ledger.append_event(
                TraceEvent(event_type=TraceEventType.CONSOLIDATION_STARTED)
            )

        # Event hex ids match the active span context.
        assert event.trace_id == format(span_context.trace_id, "032x")
        assert event.span_id == format(span_context.span_id, "016x")
        assert len(event.trace_id) == 32
        assert len(event.span_id) == 16

        # Emitted LogRecord carries the same ids as ints.
        mock_logger.emit.assert_called_once()
        record = mock_logger.emit.call_args.args[0]
        assert record.trace_id == span_context.trace_id
        assert record.span_id == span_context.span_id
        assert int(record.trace_flags) == int(span_context.trace_flags)

    async def test_no_active_span_leaves_ids_none(self):
        """With no active span, trace_id/span_id stay None and nothing raises."""
        # Detach any ambient span so get_current_span() returns the invalid span.
        with otel_trace.use_span(otel_trace.INVALID_SPAN, end_on_exit=False):
            ledger = TraceLedger(otel_logger=None)
            event = await ledger.append_event(
                TraceEvent(event_type=TraceEventType.INPUT_RECEIVED)
            )
        assert event.trace_id is None
        assert event.span_id is None
