"""Trace routes."""
from __future__ import annotations

import json
import math
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from elephantbroker.api.deps import get_container, get_trace_ledger
from elephantbroker.api.routes.trace_event_descriptions import TRACE_EVENT_DESCRIPTIONS
from elephantbroker.schemas.trace import SessionSummary, TraceEventType, TraceQuery

router = APIRouter()

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
async def list_traces(request: Request, session_id: uuid.UUID | None = None, limit: int = 100):
    await _enforce_rate_limit(request, "list")
    ledger = get_trace_ledger(request)
    gw_id = getattr(request.state, "gateway_id", None)
    query = TraceQuery(session_id=session_id, limit=limit, gateway_id=gw_id)
    events = await ledger.query_trace(query)
    return [e.model_dump(mode="json") for e in events]


@router.post("/query")
async def query_traces(query: TraceQuery, request: Request):
    # Enforce gateway isolation: the middleware-provided gateway_id always wins
    # over any caller-supplied value in the request body. `is not None` is
    # required here (not truthiness): post-Bucket-A the default gateway_id is
    # "" (empty string, falsy), and a truthiness check would silently skip the
    # override, allowing a caller to read another tenant's trace events by
    # posting {"gateway_id": "victim-tenant"}. GatewayIdentityMiddleware always
    # sets request.state.gateway_id to a string (possibly ""), so this check
    # only short-circuits when the middleware isn't wired at all.
    await _enforce_rate_limit(request, "query")
    gw_id = getattr(request.state, "gateway_id", None)
    if gw_id is not None:
        query.gateway_id = gw_id
    ledger = get_trace_ledger(request)
    events = await ledger.query_trace(query)
    return [e.model_dump(mode="json") for e in events]


@router.get("/session/{session_id}/timeline")
async def session_timeline(session_id: uuid.UUID, request: Request):
    await _enforce_rate_limit(request, "timeline")
    ledger = get_trace_ledger(request)
    gw_id = getattr(request.state, "gateway_id", None)
    query = TraceQuery(session_id=session_id, limit=10000, gateway_id=gw_id)
    events = await ledger.query_trace(query)
    groups = group_events_by_turn(events)
    return groups


@router.get("/session/{session_id}/summary")
async def session_summary(
    session_id: uuid.UUID,
    request: Request,
    no_cache: bool = Query(default=False, description="Bypass the Redis TTL cache"),
):
    await _enforce_rate_limit(request, "summary")

    # PT-2: serve from the gateway-scoped Redis TTL cache unless bypassed. The
    # cache key uses RedisKeyBuilder's gateway prefix so tenants never collide.
    container = get_container(request)
    redis = getattr(container, "redis", None)
    keys = getattr(container, "redis_keys", None)
    cache_key = (
        f"{keys.prefix}:cache:session_summary:{session_id}" if keys is not None else None
    )

    if not no_cache and redis is not None and cache_key:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # fall through to a fresh scan on any cache-read failure

    ledger = get_trace_ledger(request)
    gw_id = getattr(request.state, "gateway_id", None)
    query = TraceQuery(session_id=session_id, limit=10000, gateway_id=gw_id)
    events = await ledger.query_trace(query)

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
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List all sessions for the current gateway, sorted by most recent activity."""
    await _enforce_rate_limit(request, "sessions")
    ledger = get_trace_ledger(request)
    gateway_id = getattr(request.state, "gateway_id", None)
    result = await ledger.list_sessions(gateway_id=gateway_id, limit=limit, offset=offset)
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
async def get_trace_event(event_id: uuid.UUID, request: Request):
    await _enforce_rate_limit(request, "event")
    ledger = get_trace_ledger(request)
    gw_id = getattr(request.state, "gateway_id", None)
    events = await ledger.get_evidence_chain(event_id)
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
