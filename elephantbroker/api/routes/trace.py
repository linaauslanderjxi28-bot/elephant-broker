"""Trace routes."""
from __future__ import annotations

import json
import logging
import math
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response

from elephantbroker.api.deps import get_container, get_trace_ledger
from elephantbroker.api.routes.trace_event_descriptions import TRACE_EVENT_DESCRIPTIONS
from elephantbroker.schemas.trace import (
    SessionListResponse,
    SessionSummary,
    TraceEventType,
    TraceQuery,
)

logger = logging.getLogger("elephantbroker.api.routes.trace")

router = APIRouter()


# ---------------------------------------------------------------------------
# EB_ENABLE_TRACE_LEDGER — /trace READ-SOURCE selector
# ---------------------------------------------------------------------------
#
# The flag ONLY chooses where these routes READ from; it NEVER gates the
# ledger's write/export path (append_event -> _emit_otel_log keeps feeding
# ClickHouse regardless). Default (flag True / unset) → the in-memory
# TraceLedger (historical behaviour). Flag False → durable ClickHouse read-back
# via OtelTraceQueryClient — but only when that client is ``available``;
# otherwise we fall back to the ledger with a one-line warning so a mis-set flag
# can never break the endpoint (graceful degradation). Every response carries an
# ``X-EB-Trace-Source`` header so the source is honest without changing any
# existing response body shape (several routes return bare lists).
_TRACE_SOURCE_HEADER = "X-EB-Trace-Source"


def _resolve_trace_source(request: Request):
    """Return ``(use_clickhouse, trace_query_client)`` for the /trace read path.

    See the module note above. When the flag is False but ClickHouse is
    unavailable, logs a warning and returns ``(False, None)`` so the caller uses
    the in-memory ledger.
    """
    container = get_container(request)
    cfg = getattr(container, "config", None)
    enabled = getattr(cfg, "enable_trace_ledger", True) if cfg is not None else True
    if enabled:
        return False, None
    qc = getattr(container, "trace_query_client", None)
    if qc is not None and getattr(qc, "available", False):
        return True, qc
    logger.warning(
        "EB_ENABLE_TRACE_LEDGER=false but the ClickHouse trace query client is "
        "unavailable — falling back to the in-memory trace ledger for /trace reads"
    )
    return False, None

# ---------------------------------------------------------------------------
# PT-1: per-gateway sliding-window rate limiting
# ---------------------------------------------------------------------------
#
# The dashboard polls these endpoints, so an unbounded consumer can otherwise
# force repeated O(n) trace scans. Each endpoint gets its own request budget
# per rolling window, keyed per gateway via RedisKeyBuilder's prefix. Defaults
# below can be overridden per endpoint through an (optional) ``rate_limits``
# mapping on ``TraceConfig`` — resolved lazily so the schema can grow the field
# without this module changing.
_RATE_WINDOW_SECONDS = 60
_RATE_LIMITS: dict[str, int] = {
    "list": 120,
    "query": 120,
    "timeline": 60,
    "summary": 60,
    "sessions": 120,
    "event_types": 240,
    "event": 120,
}

# PT-2: SessionSummary Redis cache TTL (seconds). TTL-based staleness is
# acceptable for a dev/admin dashboard; `?no_cache=true` forces a fresh scan.
_SUMMARY_CACHE_TTL_SECONDS = 30


def _resolve_rate_limit(container, endpoint: str) -> int:
    """Return the request budget for *endpoint*, honouring config overrides."""
    default = _RATE_LIMITS.get(endpoint, 120)
    trace_cfg = getattr(
        getattr(getattr(container, "config", None), "infra", None), "trace", None
    )
    limits = getattr(trace_cfg, "rate_limits", None)
    if isinstance(limits, dict) and endpoint in limits:
        try:
            return int(limits[endpoint])
        except (TypeError, ValueError):
            return default
    return default


async def _enforce_rate_limit(request: Request, endpoint: str) -> None:
    """PT-1: gateway-scoped sliding-window rate limit for /trace/* endpoints.

    Uses a Redis sorted set keyed ``{prefix}:ratelimit:trace:{endpoint}`` where
    ``prefix`` already encodes the gateway (``eb:{gateway_id}``). Members are
    unique per request (timestamp + uuid); members older than the window are
    pruned before counting. On breach, raises ``429`` with a ``Retry-After``
    header derived from when the oldest in-window request will age out.

    Fail-open: if Redis (or the key builder) is unavailable, limiting is
    skipped so the trace API — the dashboard's data source — stays reachable.
    """
    container = get_container(request)
    redis = getattr(container, "redis", None)
    keys = getattr(container, "redis_keys", None)
    if redis is None or keys is None:
        return

    limit = _resolve_rate_limit(container, endpoint)
    if limit <= 0:
        return

    key = f"{keys.prefix}:ratelimit:trace:{endpoint}"
    now = time.time()
    window_start = now - _RATE_WINDOW_SECONDS

    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        results = await pipe.execute()
        count = int(results[-1]) if results else 0
    except Exception:
        return  # fail-open on any Redis error

    if count >= limit:
        retry_after = _RATE_WINDOW_SECONDS
        try:
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_score = float(oldest[0][1])
                retry_after = max(
                    1, math.ceil(oldest_score + _RATE_WINDOW_SECONDS - now)
                )
        except Exception:
            pass
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded for /trace ({endpoint}): "
                f"{limit} requests per {_RATE_WINDOW_SECONDS}s"
            ),
            headers={"Retry-After": str(retry_after)},
        )

    member = f"{now}:{uuid.uuid4()}"
    try:
        pipe = redis.pipeline()
        pipe.zadd(key, {member: now})
        pipe.expire(key, _RATE_WINDOW_SECONDS)
        await pipe.execute()
    except Exception:
        return  # fail-open — never block a request on a write failure


@router.get("/")
async def list_traces(
    request: Request,
    response: Response,
    session_id: uuid.UUID | None = None,
    limit: int = 100,
):
    await _enforce_rate_limit(request, "list")
    gw_id = getattr(request.state, "gateway_id", None)
    query = TraceQuery(session_id=session_id, limit=limit, gateway_id=gw_id)
    use_ch, qc = _resolve_trace_source(request)
    if use_ch:
        events = await qc.query_events(query)
        response.headers[_TRACE_SOURCE_HEADER] = "clickhouse"
    else:
        events = await get_trace_ledger(request).query_trace(query)
        response.headers[_TRACE_SOURCE_HEADER] = "ledger"
    return [e.model_dump(mode="json") for e in events]


@router.post("/query")
async def query_traces(query: TraceQuery, request: Request, response: Response):
    # Enforce gateway isolation: the middleware-provided gateway_id always wins
    # over any caller-supplied value in the request body. `is not None` is
    # required here (not truthiness): post-Bucket-A the default gateway_id is
    # "" (empty string, falsy), and a truthiness check would silently skip the
    # override, allowing a caller to read another tenant's trace events by
    # posting {"gateway_id": "victim-tenant"}. GatewayIdentityMiddleware always
    # sets request.state.gateway_id to a string (possibly ""), so this check
    # only short-circuits when the middleware isn't wired at all. Both read
    # sources honor this override: the ledger filters on query.gateway_id, and
    # the ClickHouse read-back stamps it as the MANDATORY %(gw)s filter.
    await _enforce_rate_limit(request, "query")
    gw_id = getattr(request.state, "gateway_id", None)
    if gw_id is not None:
        query.gateway_id = gw_id
    use_ch, qc = _resolve_trace_source(request)
    if use_ch:
        events = await qc.query_events(query)
        response.headers[_TRACE_SOURCE_HEADER] = "clickhouse"
    else:
        events = await get_trace_ledger(request).query_trace(query)
        response.headers[_TRACE_SOURCE_HEADER] = "ledger"
    return [e.model_dump(mode="json") for e in events]


@router.get("/session/{session_id}/timeline")
async def session_timeline(session_id: uuid.UUID, request: Request, response: Response):
    await _enforce_rate_limit(request, "timeline")
    gw_id = getattr(request.state, "gateway_id", None)
    use_ch, qc = _resolve_trace_source(request)
    if use_ch:
        events = await qc.session_timeline(gateway_id=gw_id, session_id=session_id)
        response.headers[_TRACE_SOURCE_HEADER] = "clickhouse"
    else:
        query = TraceQuery(session_id=session_id, limit=10000, gateway_id=gw_id)
        events = await get_trace_ledger(request).query_trace(query)
        response.headers[_TRACE_SOURCE_HEADER] = "ledger"
    groups = group_events_by_turn(events)
    return groups


@router.get("/session/{session_id}/summary")
async def session_summary(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    no_cache: bool = Query(default=False, description="Bypass the Redis TTL cache"),
):
    await _enforce_rate_limit(request, "summary")

    use_ch, qc = _resolve_trace_source(request)
    source = "clickhouse" if use_ch else "ledger"
    response.headers[_TRACE_SOURCE_HEADER] = source

    # PT-2: serve from the gateway-scoped Redis TTL cache unless bypassed. The
    # cache key uses RedisKeyBuilder's gateway prefix so tenants never collide.
    # The read source is part of the key so a ledger-cached summary is never
    # served for a ClickHouse read (and vice-versa) if the flag differs across
    # restarts sharing one Redis.
    container = get_container(request)
    redis = getattr(container, "redis", None)
    keys = getattr(container, "redis_keys", None)
    cache_key = (
        f"{keys.prefix}:cache:session_summary:{source}:{session_id}"
        if keys is not None else None
    )

    if not no_cache and redis is not None and cache_key:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # fall through to a fresh scan on any cache-read failure

    gw_id = getattr(request.state, "gateway_id", None)
    if use_ch:
        events = await qc.session_summary(gateway_id=gw_id, session_id=session_id)
    else:
        query = TraceQuery(session_id=session_id, limit=10000, gateway_id=gw_id)
        events = await get_trace_ledger(request).query_trace(query)

    event_counts: dict[str, int] = {}
    for e in events:
        event_counts[e.event_type.value] = event_counts.get(e.event_type.value, 0) + 1

    error_events = [e.model_dump(mode="json") for e in events
                    if e.event_type == TraceEventType.DEGRADED_OPERATION]

    first_at = min((e.timestamp for e in events), default=None)
    last_at = max((e.timestamp for e in events), default=None)
    duration = (last_at - first_at).total_seconds() if first_at and last_at else None

    summary = SessionSummary(
        session_id=session_id,
        total_events=len(events),
        event_counts=event_counts,
        error_events=error_events,
        first_event_at=first_at,
        last_event_at=last_at,
        duration_seconds=duration,
        turn_count=event_counts.get("after_turn_completed", 0),
        facts_extracted=event_counts.get("fact_extracted", 0),
        facts_superseded=event_counts.get("fact_superseded", 0),
        dedup_triggered=event_counts.get("dedup_triggered", 0),
        retrieval_count=event_counts.get("retrieval_performed", 0),
        compaction_count=event_counts.get("compaction_action", 0),
        guard_triggers=event_counts.get("guard_triggered", 0),
        guard_near_misses=event_counts.get("guard_near_miss", 0),
        context_assembled=event_counts.get("context_assembled", 0),
        scoring_completed=event_counts.get("scoring_completed", 0),
        successful_use_tracked=event_counts.get("successful_use_tracked", 0),
        bootstrap_completed="bootstrap_completed" in event_counts,
    )
    payload = summary.model_dump(mode="json")

    # PT-2: populate the cache for subsequent polls within the TTL window.
    if redis is not None and cache_key:
        try:
            await redis.set(
                cache_key, json.dumps(payload), ex=_SUMMARY_CACHE_TTL_SECONDS
            )
        except Exception:
            pass  # caching is best-effort — never fail the request on write

    return payload


@router.get("/sessions")
async def list_sessions(
    request: Request,
    response: Response,
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List all sessions for the current gateway, sorted by most recent activity."""
    await _enforce_rate_limit(request, "sessions")
    gateway_id = getattr(request.state, "gateway_id", None)
    use_ch, qc = _resolve_trace_source(request)
    if use_ch:
        # The ClickHouse read-back returns a flat list ordered most-recent-first;
        # fetch offset+limit rows and slice to page. total_count is the count of
        # fetched rows (capped at offset+limit) — sufficient for the admin
        # dashboard; exact global counts would need a second aggregate query.
        items = await qc.list_sessions(gateway_id=gateway_id, limit=offset + limit)
        result = SessionListResponse(
            sessions=items[offset: offset + limit],
            total_count=len(items),
        )
        response.headers[_TRACE_SOURCE_HEADER] = "clickhouse"
    else:
        result = await get_trace_ledger(request).list_sessions(
            gateway_id=gateway_id, limit=limit, offset=offset
        )
        response.headers[_TRACE_SOURCE_HEADER] = "ledger"
    return result.model_dump(mode="json")


@router.get("/event-types")
async def list_event_types(request: Request):
    """Reference endpoint — intentionally public, no gateway filtering needed.

    Still rate-limited (PT-1): the dashboard polls it, and a per-gateway budget
    keeps a runaway client from hammering the runtime even for static data.
    """
    await _enforce_rate_limit(request, "event_types")
    return [
        {"type": et.value, "description": TRACE_EVENT_DESCRIPTIONS.get(et.value, "")}
        for et in TraceEventType
    ]


@router.get("/{event_id}")
async def get_trace_event(event_id: uuid.UUID, request: Request, response: Response):
    await _enforce_rate_limit(request, "event")
    gw_id = getattr(request.state, "gateway_id", None)
    use_ch, qc = _resolve_trace_source(request)
    if use_ch:
        # The ClickHouse read-back scopes the fetch to gw_id in its SQL WHERE, so
        # a cross-gateway id resolves to None → 404 (same isolation the ledger
        # path enforces below in Python).
        event = await qc.get_event(gateway_id=gw_id, event_id=event_id)
        response.headers[_TRACE_SOURCE_HEADER] = "clickhouse"
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return event.model_dump(mode="json")

    ledger = get_trace_ledger(request)
    events = await ledger.get_evidence_chain(event_id)
    response.headers[_TRACE_SOURCE_HEADER] = "ledger"
    # `is not None` is required here — see POST /query above. Under the
    # post-Bucket-A "" middleware default, a truthiness check would bypass
    # this filter entirely and leak evidence chains across gateways.
    if gw_id is not None:
        events = [e for e in events if e.gateway_id == gw_id]
    if not events:
        raise HTTPException(status_code=404, detail="Event not found")
    return events[0].model_dump(mode="json")


def group_events_by_turn(events: list) -> list[dict]:
    """Split events into turns at turn-boundary markers."""
    sorted_events = sorted(events, key=lambda e: e.timestamp)

    has_after_turn = any(
        e.event_type == TraceEventType.AFTER_TURN_COMPLETED for e in sorted_events)
    if has_after_turn:
        boundary = TraceEventType.AFTER_TURN_COMPLETED
    elif any(e.event_type == TraceEventType.INGEST_BUFFER_FLUSH for e in sorted_events):
        boundary = TraceEventType.INGEST_BUFFER_FLUSH
    else:
        return [_make_turn_group(0, sorted_events)] if sorted_events else []

    groups: list[dict] = []
    current_group: list = []
    turn_index = 0

    for event in sorted_events:
        current_group.append(event)
        if event.event_type == boundary:
            groups.append(_make_turn_group(turn_index, current_group))
            current_group = []
            turn_index += 1

    if current_group:
        groups.append(_make_turn_group(turn_index, current_group))

    return groups


def _make_turn_group(index: int, events: list) -> dict:
    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
    return {
        "turn_index": index,
        "start_time": events[0].timestamp.isoformat() if events else None,
        "end_time": events[-1].timestamp.isoformat() if events else None,
        "event_count": len(events),
        "event_type_counts": type_counts,
        "events": [e.model_dump(mode="json") for e in events],
    }
