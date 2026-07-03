"""Dashboard API routes (Phase 11 — §11.2).

Read-heavy aggregate endpoints under ``/dashboard/*`` consumed by the Refine
dashboard data provider. They consolidate data from Neo4j (current state via
``GraphAdapter.query_cypher``), the in-memory ``TraceLedger`` (event stream),
Redis (active sessions / pending approvals), and existing runtime modules
(``MemoryStoreFacade``, ``CustomRuleStore``, ``ProfileRegistry``, ...).

Design rules honoured here:
- ``gateway_id`` is always read from ``request.state`` (stamped + tenant-checked
  by ``GatewayIdentityMiddleware``) and passed explicitly to every store/facade.
  A caller-supplied gateway in a body/query is never trusted.
- All module-level Cypher is gateway-scoped: ``WHERE ... f.gateway_id = $gw``.
- Every endpoint degrades gracefully: when a data source is unavailable at
  runtime the handler returns empty/default payloads (with a ``note`` where the
  response shape allows) instead of raising.
- Auth: read endpoints require authority >= 70; mutations and privileged views
  (guard-rule writes, effective config) require >= 90. See the SOW authority
  matrix. Enforcement is via the shared ``require_authority`` dependency.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from elephantbroker.api.deps import (
    get_container,
    get_guard_engine,
    get_memory_store,
    get_profile_registry,
    get_redis_keys,
    get_trace_ledger,
)
from elephantbroker.schemas.base import PaginatedResult
from elephantbroker.schemas.dashboard import (
    ActiveSessionSummary,
    ActorDetailResponse,
    ActorFactCount,
    ActorSummary,
    ComponentHealth,
    DashboardOverview,
    FactDetailResponse,
    FactEdge,
    FactUsageSummary,
    GatewayInfo,
    GoalSummary,
    GraphEdge,
    GraphNode,
    GuardActivityResponse,
    GuardRuleUpdate,
    KnowledgeGraphResponse,
    LinkedClaim,
    MemoryBrowseRequest,
    MemoryStatsResponse,
    OrganizationSummary,
    ProcedureDetailResponse,
    ProcedureSummary,
    ProfileSummary,
    RecentEvent,
    SavedView,
    SavedViewCreate,
    TimeBucket,
    UserPreferences,
)
from elephantbroker.schemas.fact import FactAssertion
from elephantbroker.schemas.guards import StaticRule

logger = logging.getLogger("elephantbroker.api.routes.dashboard")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Auth dependency (import-safe — the auth workstream owns require_authority).
# If the auth layer is unavailable at import time (dep not installed / module
# not yet landed), fall back to a permissive dependency so the dashboard still
# imports and serves rather than crashing the whole app. Enforcement returns
# once the auth module is present.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised via integration
    from elephantbroker.api.auth import require_authority
except Exception:  # noqa: BLE001 - degrade gracefully when auth layer absent
    logger.warning(
        "Auth layer unavailable — /dashboard routes are running WITHOUT "
        "authority enforcement (permissive fallback). This must never happen "
        "in production.",
        exc_info=True,
    )

    def require_authority(min_level: int):  # type: ignore[misc]
        async def _dep(request: Request):
            return None

        return _dep


READ = Depends(require_authority(70))
WRITE = Depends(require_authority(90))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIME_RANGES: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def _gateway_id(request: Request) -> str:
    """Canonical read of the tenant gateway (middleware always stamps a str)."""
    return getattr(request.state, "gateway_id", "")


def _actor_id(request: Request) -> str:
    """Resolve the calling actor for per-actor scoping (preferences/views)."""
    identity = getattr(request.state, "identity", None)
    if identity is not None:
        aid = getattr(identity, "actor_id", None)
        if aid:
            return str(aid)
    hdr = request.headers.get("X-EB-Actor-Id")
    return hdr or "anonymous"


def _range_start(time_range: str) -> tuple[str, datetime]:
    """Normalise a time_range param and return (canonical, from_timestamp)."""
    tr = time_range if time_range in _TIME_RANGES else "24h"
    return tr, datetime.now(UTC) - _TIME_RANGES[tr]


def _epoch_ms_to_dt(ms) -> datetime | None:
    """Convert a stored ``eb_created_at``/``eb_updated_at`` epoch-ms int to UTC datetime."""
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return datetime.fromtimestamp(v / 1000.0, tz=UTC)


def _short_id(value) -> str:
    """8-char prefix of an id, mirroring the frontend ``shortId`` helper."""
    s = str(value or "")
    return s[:8] if s else ""


def _actor_label(display_name, actor_id) -> str:
    """Resolve a human label for an actor from its registry ``display_name``.

    Returns the registered ``display_name`` when present (the frontend
    ``actorDisplayName`` helper further trims any ``dashboard:<uuid>`` handle),
    else a short id prefix so the UI never renders a bare full UUID.
    """
    dn = str(display_name or "").strip()
    if dn:
        return dn
    return _short_id(actor_id) or str(actor_id or "")


def _get_redis(container):
    return getattr(container, "redis", None)


def _get_custom_rule_store(container):
    return getattr(container, "custom_rule_store", None)


def _invalidate_guard_rules_probe(container) -> None:
    """Best-effort: nudge the in-process guard engine to re-probe the
    CustomRuleStore version on its next preflight (FIX-4).

    Called after a successful rule create/update/delete so a same-process rule
    change enforces on the very next guard check — the configured
    ``guards.custom_rule_refresh_seconds`` interval then only governs
    cross-process staleness. Silently a no-op when no engine is wired.
    """
    engine = getattr(container, "guard_engine", None)
    invalidate = getattr(engine, "invalidate_custom_rules_probe", None)
    if not callable(invalidate):
        return
    try:
        invalidate()
    except Exception as exc:  # noqa: BLE001
        logger.debug("dashboard: guard rules probe invalidation failed: %s", exc)


def _get_prefs_store(container):
    for attr in ("dashboard_preferences_store", "preferences_store", "dashboard_store"):
        store = getattr(container, attr, None)
        if store is not None:
            return store
    return None


async def _smembers(redis, key: str) -> list[str]:
    """Read a Redis SET as decoded strings; degrade to [] on any failure."""
    if redis is None:
        return []
    try:
        members = await redis.smembers(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: redis smembers(%s) failed: %s", key, exc)
        return []
    out: list[str] = []
    for m in members or []:
        out.append(m.decode() if isinstance(m, bytes) else str(m))
    return out


async def _cypher(container, query: str, params: dict) -> list[dict]:
    """Run a gateway-scoped read query; degrade to [] when graph unavailable."""
    graph = getattr(container, "graph", None)
    if graph is None:
        return []
    try:
        return await graph.query_cypher(query, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: cypher failed: %s", exc)
        return []


# Trace-event → human-readable summary for the overview activity feed.
def _summarize_event(event_type: str, payload: dict) -> str:
    p = payload or {}
    mapping = {
        "fact_extracted": lambda: f"New fact extracted: {str(p.get('text', ''))[:60]}"
        if p.get("text")
        else f"Facts extracted: {p.get('facts_count', '?')}",
        "retrieval_performed": lambda: f"Memory search: {p.get('result_count', p.get('candidate_count', '?'))} results",
        "context_assembled": lambda: f"Context assembled: {p.get('total_tokens', '?')} tokens",
        "guard_triggered": lambda: f"Guard triggered: {p.get('action', p.get('outcome', '?'))} blocked",
        "guard_near_miss": lambda: f"Guard near-miss: {p.get('action', '?')}",
        "scoring_completed": lambda: f"Scoring: {p.get('candidate_count', '?')} candidates ranked",
        "compaction_action": lambda: f"Compaction: {p.get('trigger', '?')}",
        "degraded_operation": lambda: f"Error: {p.get('error', 'unknown')}",
        "session_boundary": lambda: "Session ended",
        "bootstrap_completed": lambda: f"Session started: profile={p.get('profile_name', '?')}",
    }
    fn = mapping.get(event_type)
    try:
        return fn() if fn else event_type
    except Exception:  # noqa: BLE001
        return event_type


# Low-level / high-frequency internal event types that flood the overview
# activity feed (one retrieval fans out into many ``retrieval_source_result``
# rows, etc.). They are excluded from the *feed* only — period counters above
# still aggregate over the full event stream. Keeps one search = one feed row.
_FEED_NOISE_EVENT_TYPES: frozenset[str] = frozenset({
    "retrieval_source_result",
    "scoring_completed",
    "memory_class_assigned",
    "token_usage_reported",
    "context_window_reported",
    "successful_use_tracked",
    "cognee_cognify_completed",
    "ingest_buffer_flush",
    "handle_resolved",
    "subagent_parent_mapped",
    "profile_resolved",
    "after_turn_completed",
    "procedure_completion_checked",
    "constraint_reinjected",
})


# --- Effective-config secret masking (authority >= 90 view) ----------------
# Mask only genuine credentials. A naive substring match ("token" in
# "max_tokens", "api_key" in "api_keys_db_path") over-masks numeric limits and
# filesystem paths, hiding harmless config from operators. We match secret
# *words* on token boundaries plus a small set of known compounds, and never
# mask keys that denote a location/limit.
_SECRET_WORD_TOKENS: frozenset[str] = frozenset(
    {"password", "passwd", "secret", "token", "credential", "credentials"}
)
_SECRET_COMPOUNDS: tuple[str, ...] = (
    "api_key", "apikey", "private_key", "privatekey", "access_token",
    "refresh_token", "client_secret", "hmac_secret", "dsn",
)
_NONSECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "_path", "_dir", "_file", "_url", "_uri", "_host", "_port",
)


def _is_secret_key(key: str) -> bool:
    """True only when ``key`` names a real credential (not a path/limit).

    ``max_tokens`` → False (numeric limit; ``tokens`` != the word ``token``).
    ``api_keys_db_path`` → False (``_path`` suffix = location).
    ``neo4j_password`` / ``api_key`` / ``callback_hmac_secret`` → True.
    """
    k = str(key).lower()
    if k.endswith(_NONSECRET_KEY_SUFFIXES):
        return False
    words = {w for w in re.split(r"[^a-z0-9]+", k) if w}
    if words & _SECRET_WORD_TOKENS:
        return True
    return any(compound in k for compound in _SECRET_COMPOUNDS)


def _mask_config(value):
    """Recursively mask real secret values in a config dump."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_secret_key(k) and isinstance(v, str) and v:
                out[k] = "***MASKED***"
            else:
                out[k] = _mask_config(v)
        return out
    if isinstance(value, list):
        return [_mask_config(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Overview & system
# ---------------------------------------------------------------------------


@router.get("/overview", dependencies=[READ])
async def overview(request: Request, time_range: str = Query("24h")):
    """Landing-page aggregate: counts, guard/error stats, health, recent feed."""
    container = get_container(request)
    ledger = get_trace_ledger(request)
    gw = _gateway_id(request)
    tr, since = _range_start(time_range)

    # --- Entity + fact counts (Neo4j current state) ---
    total_facts = 0
    facts_by_class: dict[str, int] = {}
    facts_by_scope: dict[str, int] = {}
    rows = await _cypher(
        container,
        "MATCH (f:FactDataPoint) WHERE f.gateway_id = $gw "
        "RETURN f.memory_class AS mc, f.scope AS sc, count(f) AS cnt",
        {"gw": gw},
    )
    for r in rows:
        cnt = int(r.get("cnt", 0) or 0)
        total_facts += cnt
        mc = r.get("mc") or "unknown"
        sc = r.get("sc") or "unknown"
        facts_by_class[mc] = facts_by_class.get(mc, 0) + cnt
        facts_by_scope[sc] = facts_by_scope.get(sc, 0) + cnt

    actor_rows = await _cypher(
        container,
        "MATCH (a:ActorDataPoint) WHERE a.gateway_id = $gw "
        "AND (a.active = true OR a.active IS NULL) RETURN count(a) AS cnt",
        {"gw": gw},
    )
    total_actors = int(actor_rows[0].get("cnt", 0)) if actor_rows else 0

    org_rows = await _cypher(
        container, "MATCH (o:OrganizationDataPoint) RETURN count(o) AS cnt", {}
    )
    total_orgs = int(org_rows[0].get("cnt", 0)) if org_rows else 0

    goal_rows = await _cypher(
        container,
        "MATCH (g:GoalDataPoint) WHERE g.gateway_id = $gw AND g.status = 'active' "
        "RETURN count(g) AS cnt",
        {"gw": gw},
    )
    total_goals = int(goal_rows[0].get("cnt", 0)) if goal_rows else 0

    # --- Active sessions (Redis SET) ---
    keys = get_redis_keys(request)
    redis = _get_redis(container)
    active_sessions = 0
    if redis is not None and keys is not None:
        try:
            active_sessions = int(await redis.scard(keys.active_sessions()) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: scard(active_sessions) failed: %s", exc)

    # --- Period event stats (trace ledger) ---
    facts_in_period = 0
    guard_triggers = 0
    guard_near_misses = 0
    errors = 0
    recent_events: list[RecentEvent] = []
    if ledger is not None:
        from elephantbroker.schemas.trace import TraceQuery

        try:
            events = await ledger.query_trace(
                TraceQuery(gateway_id=gw, from_timestamp=since, limit=10000)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: query_trace failed: %s", exc)
            events = []
        for e in events:
            et = e.event_type.value
            if et == "fact_extracted":
                facts_in_period += int((e.payload or {}).get("facts_count", 1) or 1)
            elif et == "guard_triggered":
                guard_triggers += 1
            elif et == "guard_near_miss":
                guard_near_misses += 1
            elif et == "degraded_operation":
                errors += 1
        # Feed: drop low-level/internal noise so one search is one row, not six
        # (overview-3). Period counters above still see the full stream.
        feed_events = [
            e for e in events if e.event_type.value not in _FEED_NOISE_EVENT_TYPES
        ]
        for e in sorted(feed_events, key=lambda x: x.timestamp, reverse=True)[:10]:
            recent_events.append(
                RecentEvent(
                    timestamp=e.timestamp,
                    summary=_summarize_event(e.event_type.value, e.payload or {}),
                    event_type=e.event_type.value,
                    session_key=e.session_key,
                )
            )

    # --- Component health (live probes, best-effort) ---
    components = await _probe_components(container)
    statuses = [c.status for c in components.values()]
    if any(s == "error" for s in statuses):
        system_health = "unhealthy" if errors > 0 else "degraded"
    elif errors > 0 or guard_triggers > 0:
        system_health = "degraded"
    else:
        system_health = "healthy"

    return DashboardOverview(
        time_range=tr,
        total_facts=total_facts,
        facts_in_period=facts_in_period,
        facts_by_class=facts_by_class,
        facts_by_scope=facts_by_scope,
        active_sessions=active_sessions,
        total_actors=total_actors,
        total_organizations=total_orgs,
        total_goals_active=total_goals,
        guard_triggers_in_period=guard_triggers,
        guard_near_misses_in_period=guard_near_misses,
        errors_in_period=errors,
        system_health=system_health,
        components=components,
        recent_events=recent_events,
    ).model_dump(mode="json")


async def _probe_components(container) -> dict[str, ComponentHealth]:
    """Cheap live probes for the 5 infrastructure components."""
    import time as _time

    comps: dict[str, ComponentHealth] = {}

    graph = getattr(container, "graph", None)
    if graph is not None:
        t0 = _time.monotonic()
        try:
            await graph.query_cypher("RETURN 1", {})
            comps["neo4j"] = ComponentHealth(status="ok", latency_ms=round((_time.monotonic() - t0) * 1000, 2))
        except Exception:  # noqa: BLE001
            comps["neo4j"] = ComponentHealth(status="error")
    else:
        comps["neo4j"] = ComponentHealth(status="not configured")

    vector = getattr(container, "vector", None)
    if vector is not None:
        t0 = _time.monotonic()
        try:
            await vector.ping()
            comps["qdrant"] = ComponentHealth(status="ok", latency_ms=round((_time.monotonic() - t0) * 1000, 2))
        except Exception:  # noqa: BLE001
            comps["qdrant"] = ComponentHealth(status="error")
    else:
        comps["qdrant"] = ComponentHealth(status="not configured")

    redis = getattr(container, "redis", None)
    if redis is not None:
        t0 = _time.monotonic()
        try:
            await redis.ping()
            comps["redis"] = ComponentHealth(status="ok", latency_ms=round((_time.monotonic() - t0) * 1000, 2))
        except Exception:  # noqa: BLE001
            comps["redis"] = ComponentHealth(status="error")
    else:
        comps["redis"] = ComponentHealth(status="not configured")

    comps["llm"] = ComponentHealth(status="ok" if getattr(container, "llm_client", None) else "not configured")
    comps["embedding"] = ComponentHealth(status="ok" if getattr(container, "embeddings", None) else "not configured")
    return comps


@router.get("/gateways", dependencies=[READ])
async def gateways(request: Request):
    """List available gateways. Single-tenant-per-process → current gateway."""
    container = get_container(request)
    current = _gateway_id(request) or getattr(container, "gateway_id", "")
    gw_cfg = getattr(getattr(container, "config", None), "gateway", None)
    org_id = getattr(gw_cfg, "org_id", None) if gw_cfg else None
    info = GatewayInfo(gateway_id=current, org_id=org_id, is_current=True)
    return {"gateways": [info.model_dump(mode="json")]}


@router.get("/config/effective", dependencies=[WRITE])
async def config_effective(request: Request):
    """Return the resolved config with secrets masked (authority >= 90)."""
    container = get_container(request)
    cfg = getattr(container, "config", None)
    if cfg is None:
        return {"config": {}, "note": "config not available"}
    try:
        dumped = cfg.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: config dump failed: %s", exc)
        return {"config": {}, "note": "config dump failed"}
    return {"config": _mask_config(dumped)}


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


@router.post("/memory/browse", dependencies=[READ])
async def memory_browse(body: MemoryBrowseRequest, request: Request):
    """Paginated, filtered, sorted fact listing → PaginatedResult[FactAssertion].

    Primary path calls ``MemoryStoreFacade.query_facts`` (the frozen Phase 11
    facade interface). Two request filters (``min_confidence``, ``goal_id``)
    are not part of the frozen ``FactFilters`` surface; they are applied as
    best-effort page-level refinements here.
    """
    ms = get_memory_store(request)
    gw = _gateway_id(request)
    offset = (body.page - 1) * body.per_page

    if ms is None or not hasattr(ms, "query_facts"):
        return PaginatedResult[FactAssertion](
            items=[], total=0, offset=offset, limit=body.per_page, has_more=False
        ).model_dump(mode="json")

    # Lazy import of the facade query schemas (owned by the facade workstream).
    from elephantbroker.schemas.fact import FactFilters, FactSort, FactSortField

    # Sortable columns == the whitelisted FactSortField enum (the facade's
    # injection-safe ORDER BY boundary). Deriving the map from the enum means a
    # later enum extension (category / session_key / successful_use_count — see
    # crossFileNeeds) becomes sortable here with zero code change. Columns the
    # UI offers but the facade can't honor fall back to created_at, and the
    # response echoes ``applied_sort_*`` + ``sortable_fields`` so the grid
    # reflects reality and can restrict its menu instead of silently sorting by
    # the wrong column (memory-browse-6). The old map aliased
    # successful_use_count -> use_count, which is exactly that silent mis-sort.
    sort_map = {f.value: f for f in FactSortField}
    sortable_fields = list(sort_map.keys())
    requested_sort = body.sort_by or "created_at"
    sort_field = sort_map.get(requested_sort, FactSortField.CREATED_AT)
    applied_sort_by = (
        requested_sort if requested_sort in sort_map else FactSortField.CREATED_AT.value
    )
    sort_order = "asc" if (body.sort_order or "desc").lower() == "asc" else "desc"

    filters = FactFilters(
        scope=body.scope,
        memory_class=body.memory_class,
        category=body.category.value if body.category else None,
        actor_id=str(body.source_actor_id) if body.source_actor_id else None,
        session_key=body.session_key,
        text_contains=body.text_contains,
        # min_confidence IS a first-class FactFilters field — push it into the
        # facade so ``total`` is computed AFTER filtering (rows == count).
        # Applying it post-page (the old path) left the footer counting facts
        # the page had already dropped (memory-browse-5).
        min_confidence=body.min_confidence,
    )
    sort = FactSort(field=sort_field, descending=(sort_order != "asc"))

    try:
        page = await ms.query_facts(
            gateway_id=gw,
            filters=filters,
            page=body.page,
            page_size=body.per_page,
            sort=sort,
        )
        items = list(page.items)
        total = page.total
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: query_facts failed: %s", exc)
        items, total = [], 0

    # goal_id is an edge filter with no FactFilters equivalent; applied as a
    # best-effort page-level refinement. Its total/has_more skew is tracked
    # under memory-browse-2 (a frontend double-fetch fix outside this file).
    if body.goal_id is not None:
        container = get_container(request)
        goal_rows = await _cypher(
            container,
            "MATCH (f:FactDataPoint)-[:SERVES_GOAL]->(g {eb_id: $goal_id}) "
            "WHERE f.gateway_id = $gw RETURN f.eb_id AS id",
            {"goal_id": str(body.goal_id), "gw": gw},
        )
        goal_ids = {str(r.get("id")) for r in goal_rows}
        items = [f for f in items if str(f.id) in goal_ids]

    has_more = (offset + len(items)) < total
    result = PaginatedResult[FactAssertion](
        items=items, total=total, offset=offset, limit=body.per_page, has_more=has_more
    ).model_dump(mode="json")
    # Additive sort metadata (safe for existing consumers, which read items/total
    # only): lets the grid reflect the sort actually applied and restrict its
    # sortable columns to the set the backend can honor.
    result["applied_sort_by"] = applied_sort_by
    result["applied_sort_order"] = sort_order
    result["sortable_fields"] = sortable_fields
    return result


@router.get("/memory/{fact_id}/detail", dependencies=[READ])
async def memory_detail(fact_id: uuid.UUID, request: Request):
    """Fact + graph edges + linked claims + usage summary + trace link."""
    container = get_container(request)
    gw = _gateway_id(request)

    # ``target_type`` picks the meaningful DataPoint label — ``labels(x)[0]``
    # often returns Cognee's internal ``__Node__`` (dead "Connections" links,
    # gap-4-6). ``target_label`` also coalesces ``claim_text`` so claim edges
    # render their text instead of a bare UUID.
    _ttype = (
        "coalesce(head([l IN labels({n}) WHERE l ENDS WITH 'DataPoint']), "
        "head([l IN labels({n}) WHERE NOT l STARTS WITH '__' AND l <> 'DataPoint' "
        "AND l <> 'Entity']), head(labels({n})))"
    )
    _tlabel = (
        "coalesce({n}.display_name, {n}.title, {n}.name, {n}.claim_text, "
        "left({n}.text, 60), {n}.eb_id)"
    )
    rows = await _cypher(
        container,
        "MATCH (f:FactDataPoint {eb_id: $fid, gateway_id: $gw}) "
        "OPTIONAL MATCH (f)-[r]->(t) "
        "WITH f, collect({relation_type: type(r), direction: 'outgoing', "
        "target_id: t.eb_id, target_type: " + _ttype.format(n="t") + ", "
        "target_label: " + _tlabel.format(n="t") + ", "
        "target_properties: properties(t)}) AS outgoing "
        "OPTIONAL MATCH (s)-[r2]->(f) "
        "WITH f, outgoing, collect({relation_type: type(r2), direction: 'incoming', "
        "target_id: s.eb_id, target_type: " + _ttype.format(n="s") + ", "
        "target_label: " + _tlabel.format(n="s") + ", "
        "target_properties: properties(s)}) AS incoming "
        "RETURN properties(f) AS fact, outgoing + incoming AS edges",
        {"fid": str(fact_id), "gw": gw},
    )
    if not rows:
        return JSONResponse(status_code=404, content={"detail": "Fact not found"})

    fact_props = rows[0].get("fact") or {}
    raw_edges = rows[0].get("edges") or []

    edges: list[FactEdge] = []
    claims: list[LinkedClaim] = []
    superseded_by: str | None = None
    for e in raw_edges:
        rel = e.get("relation_type")
        if not rel:  # OPTIONAL MATCH with no hit yields null-populated rows
            continue
        tp = e.get("target_properties") or {}
        edges.append(
            FactEdge(
                relation_type=rel,
                direction=e.get("direction") or "outgoing",
                target_id=e.get("target_id"),
                target_type=e.get("target_type"),
                target_label=e.get("target_label") or "",
                target_properties=tp if isinstance(tp, dict) else {},
            )
        )
        # A linked claim is any edge whose target node is a ClaimDataPoint —
        # keyed on the node label, not a specific edge type/direction, so this
        # read survives whatever fact<->claim edge the evidence engine wires
        # (gap-4-1, currently unwritten). ClaimDataPoint stores ``claim_text``,
        # not ``text`` (gap-4-2).
        if str(e.get("target_type") or "") == "ClaimDataPoint":
            claims.append(
                LinkedClaim(
                    claim_id=str(e.get("target_id") or ""),
                    claim_text=str(tp.get("claim_text") or tp.get("text") or "")[:200],
                    status=str(tp.get("status") or ""),
                    evidence_count=int(tp.get("evidence_count") or 0),
                )
            )
        if rel == "SUPERSEDES" and e.get("direction") == "incoming":
            superseded_by = e.get("target_id")

    # Real evidence count from the edges the engine actually writes:
    # (:EvidenceDataPoint)-[:SUPPORTS]->(:ClaimDataPoint). The claim node carries
    # no denormalized ``evidence_count`` property, so count the edges (gap-4-3).
    if claims:
        cids = [c.claim_id for c in claims if c.claim_id]
        if cids:
            ev_rows = await _cypher(
                container,
                "MATCH (e:EvidenceDataPoint)-[:SUPPORTS]->(c:ClaimDataPoint) "
                "WHERE c.gateway_id = $gw AND c.eb_id IN $cids "
                "RETURN c.eb_id AS cid, count(e) AS n",
                {"gw": gw, "cids": cids},
            )
            counts = {str(r.get("cid")): int(r.get("n", 0) or 0) for r in ev_rows}
            for c in claims:
                if c.claim_id in counts:
                    c.evidence_count = counts[c.claim_id]

    fact = _fact_from_props(fact_props)
    usage = FactUsageSummary(
        use_count=fact.use_count,
        successful_use_count=fact.successful_use_count,
        success_rate=round(fact.successful_use_count / max(fact.use_count, 1) * 100, 2),
        last_used_at=fact.last_used_at,
        superseded_by=superseded_by,
        goal_relevance_tags=fact.goal_relevance_tags,
    )

    # Link back to the extraction trace event via FACT_EXTRACTED.fact_ids.
    extraction_event_id: uuid.UUID | None = None
    ledger = get_trace_ledger(request)
    if ledger is not None:
        from elephantbroker.schemas.trace import TraceQuery

        try:
            events = await ledger.query_trace(TraceQuery(gateway_id=gw, limit=10000))
            for e in events:
                if e.event_type.value != "fact_extracted":
                    continue
                fids = (e.payload or {}).get("fact_ids") or []
                if str(fact_id) in [str(x) for x in fids]:
                    extraction_event_id = e.id
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: extraction event lookup failed: %s", exc)

    return FactDetailResponse(
        fact=fact,
        edges=edges,
        claims=claims,
        usage=usage,
        session_key=fact.session_key,
        extraction_trace_event_id=extraction_event_id,
    ).model_dump(mode="json")


def _fact_from_props(props: dict) -> FactAssertion:
    """Reconstruct a FactAssertion from Neo4j node props.

    Delegates to the canonical ``FactDataPoint(**clean_graph_props(props))
    .to_schema()`` path the facade uses, so stored fields survive —
    ``eb_created_at`` / ``eb_updated_at`` map back to created_at/updated_at,
    plus source_actor_id, session_id, goal_ids, last-used, etc. The previous
    hand-rolled copy never read those keys, so ``FactAssertion`` fell back to
    ``now()`` and the detail page always showed "created less than a minute
    ago" while nulling the real provenance fields (memory-browse-3).
    """
    p = dict(props or {})
    try:
        from elephantbroker.runtime.adapters.cognee.datapoints import FactDataPoint
        from elephantbroker.runtime.graph_utils import clean_graph_props

        return FactDataPoint(**clean_graph_props(p)).to_schema()
    except Exception:  # noqa: BLE001 - fall back to a tolerant partial rebuild
        logger.debug(
            "dashboard: canonical fact rebuild failed; partial fallback", exc_info=True
        )

    data: dict = {}
    fid = p.get("eb_id") or p.get("id")
    if fid:
        try:
            data["id"] = uuid.UUID(str(fid))
        except Exception:  # noqa: BLE001
            pass
    for key in (
        "text", "category", "scope", "memory_class", "session_key",
        "decision_domain",
    ):
        if p.get(key) is not None:
            data[key] = p[key]
    if p.get("confidence") is not None:
        try:
            data["confidence"] = float(p["confidence"])
        except Exception:  # noqa: BLE001
            pass
    for key in ("use_count", "successful_use_count"):
        if p.get(key) is not None:
            try:
                data[key] = int(p[key])
            except Exception:  # noqa: BLE001
                pass
    for key in ("archived", "autorecall_blacklisted"):
        if p.get(key) is not None:
            data[key] = bool(p[key])
    # Preserve the stored timestamps even on the fallback path so we never
    # fabricate "just now".
    created = _epoch_ms_to_dt(p.get("eb_created_at"))
    if created is not None:
        data["created_at"] = created
    updated = _epoch_ms_to_dt(p.get("eb_updated_at"))
    if updated is not None:
        data["updated_at"] = updated
    if p.get("gateway_id") is not None:
        data["gateway_id"] = p["gateway_id"]
    if not data.get("text"):
        data["text"] = p.get("text") or "(unknown)"
    try:
        return FactAssertion(**data)
    except Exception:  # noqa: BLE001
        return FactAssertion(text=str(p.get("text") or "(unknown)"))


@router.get("/memory/stats", dependencies=[READ])
async def memory_stats(request: Request, time_range: str = Query("24h")):
    """Current-state aggregates (Neo4j) + activity rates (trace stream)."""
    container = get_container(request)
    gw = _gateway_id(request)
    tr, since = _range_start(time_range)

    total_facts = 0
    by_class: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    rows = await _cypher(
        container,
        "MATCH (f:FactDataPoint) WHERE f.gateway_id = $gw "
        "RETURN f.memory_class AS mc, f.scope AS sc, count(f) AS cnt",
        {"gw": gw},
    )
    for r in rows:
        cnt = int(r.get("cnt", 0) or 0)
        total_facts += cnt
        by_class[r.get("mc") or "unknown"] = by_class.get(r.get("mc") or "unknown", 0) + cnt
        by_scope[r.get("sc") or "unknown"] = by_scope.get(r.get("sc") or "unknown", 0) + cnt

    agg = await _cypher(
        container,
        "MATCH (f:FactDataPoint) WHERE f.gateway_id = $gw "
        "RETURN avg(f.confidence) AS avg_conf, avg(f.use_count) AS avg_use",
        {"gw": gw},
    )
    avg_conf = float(agg[0].get("avg_conf") or 0.0) if agg else 0.0
    avg_use = float(agg[0].get("avg_use") or 0.0) if agg else 0.0

    # Success rate honestly: average only over facts that have actually been
    # used. Never-used facts carry no success/failure signal — the old query
    # counted them as 0% success (use_count 0 -> 0/1), dragging the average to
    # zero and framing untested facts as failures (memory-stats-6). ``facts_with
    # _usage`` lets the UI render "N/A" instead of a misleading 0%.
    succ = await _cypher(
        container,
        "MATCH (f:FactDataPoint) WHERE f.gateway_id = $gw AND coalesce(f.use_count, 0) > 0 "
        "RETURN avg(toFloat(coalesce(f.successful_use_count, 0)) / f.use_count) AS avg_success, "
        "count(f) AS used_count",
        {"gw": gw},
    )
    avg_success = float(succ[0].get("avg_success") or 0.0) if succ else 0.0
    facts_with_usage = int(succ[0].get("used_count") or 0) if succ else 0

    # Top actors by owned-fact count. Skip empty-string source ids (the blank
    # first row, memory-stats-2) and resolve each actor's registry display_name
    # so the column shows a name, not a raw UUID (memory-stats-3).
    top_rows = await _cypher(
        container,
        "MATCH (f:FactDataPoint) WHERE f.gateway_id = $gw "
        "AND f.source_actor_id IS NOT NULL AND trim(f.source_actor_id) <> '' "
        "WITH f.source_actor_id AS aid, count(f) AS cnt "
        "ORDER BY cnt DESC LIMIT 10 "
        "OPTIONAL MATCH (a:ActorDataPoint {eb_id: aid, gateway_id: $gw}) "
        "RETURN aid, cnt, a.display_name AS display_name",
        {"gw": gw},
    )
    top_actors = [
        ActorFactCount(
            actor_id=str(r.get("aid")),
            actor_label=_actor_label(r.get("display_name"), r.get("aid")),
            fact_count=int(r.get("cnt", 0) or 0),
        )
        for r in top_rows
    ]

    # Activity rates + sparkline. Prefer the DURABLE store (ClickHouse via the
    # OtelTraceQueryClient) so wide ranges (6h/24h/7d) reflect real cross-session
    # history instead of only the bounded in-memory trace buffer (memory-stats-1).
    # Fall back to the ledger ONLY when the durable store is unavailable, and
    # record the source actually used so the footer is truthful in both modes
    # (memory-stats-5). The ledger is fetched up front so the fallback path and
    # the retention-window computation share one handle.
    extractions = 0
    dedups = 0
    supersessions = 0
    buckets: dict[datetime, int] = {}
    ledger = get_trace_ledger(request)

    durable = None
    trace_qc = getattr(container, "trace_query_client", None)
    if trace_qc is not None and getattr(trace_qc, "available", False):
        try:
            durable = await trace_qc.get_activity_stats(gateway_id=gw, since=since)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: durable activity stats failed: %s", exc)
            durable = None

    if durable is not None:
        activity_source = "clickhouse"
        extractions = int(durable.get("extractions", 0) or 0)
        dedups = int(durable.get("dedups", 0) or 0)
        supersessions = int(durable.get("supersessions", 0) or 0)
        for b in durable.get("buckets", []) or []:
            ts = b.get("timestamp")
            if ts is None:
                continue
            buckets[ts] = buckets.get(ts, 0) + int(b.get("count", 0) or 0)
    else:
        activity_source = "ledger"
        if ledger is not None:
            from elephantbroker.schemas.trace import TraceQuery

            try:
                events = await ledger.query_trace(
                    TraceQuery(gateway_id=gw, from_timestamp=since, limit=10000)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("dashboard: stats query_trace failed: %s", exc)
                events = []
            for e in events:
                et = e.event_type.value
                if et == "fact_extracted":
                    n = int((e.payload or {}).get("facts_count", 1) or 1)
                    extractions += n
                    bucket = e.timestamp.replace(minute=0, second=0, microsecond=0)
                    buckets[bucket] = buckets.get(bucket, 0) + n
                elif et == "dedup_triggered":
                    dedups += 1
                elif et == "fact_superseded":
                    supersessions += 1

    dedup_rate = round(dedups / extractions, 4) if extractions else 0.0
    supersession_rate = round(supersessions / extractions, 4) if extractions else 0.0
    creation_over_time = [
        TimeBucket(timestamp=ts, count=c) for ts, c in sorted(buckets.items())
    ]

    resp = MemoryStatsResponse(
        time_range=tr,
        total_facts=total_facts,
        by_class=by_class,
        by_scope=by_scope,
        avg_confidence=round(avg_conf, 4),
        avg_use_count=round(avg_use, 4),
        avg_success_rate=round(avg_success, 4),
        top_actors=top_actors,
        extractions_in_period=extractions,
        dedup_rate=dedup_rate,
        supersession_rate=supersession_rate,
        creation_over_time=creation_over_time,
    ).model_dump(mode="json")
    # Additive honesty metadata (safe for existing consumers, which read the
    # typed fields only).
    resp["facts_with_usage"] = facts_with_usage

    # Truthful activity-source labelling (memory-stats-1/5): the footer must
    # state the source that ACTUALLY served the activity data at runtime — never
    # a hardcoded claim.
    resp["activity_source"] = activity_source
    if activity_source == "clickhouse":
        # The durable store serves the full requested window — nothing is capped.
        resp["activity_source_label"] = "ClickHouse (durable)"
        resp["activity_retention_seconds"] = None
        resp["activity_window_capped"] = False
        resp["note"] = (
            "Activity rates and the creation sparkline are served from the "
            "durable ClickHouse trace store over the full selected range."
        )
    else:
        # In-memory trace buffer retention (RC-5): the ledger keeps only the last
        # ``memory_ttl_seconds`` of events, so activity for a wider range can only
        # ever reflect the retained window. Surface the effective window + a note
        # so the UI can annotate (or cap) ranges the buffer cannot serve.
        resp["activity_source_label"] = "in-memory trace ledger"
        retention_s = (
            int(getattr(ledger, "_ttl_seconds", 3600) or 3600) if ledger is not None else 3600
        )
        requested_s = int(_TIME_RANGES[tr].total_seconds())
        activity_capped = requested_s > retention_s
        resp["activity_retention_seconds"] = retention_s
        resp["activity_window_capped"] = activity_capped
        if activity_capped:
            if retention_s >= 3600:
                window_txt = f"{retention_s // 3600}h"
            elif retention_s >= 60:
                window_txt = f"{retention_s // 60}m"
            else:
                window_txt = f"{retention_s}s"
            resp["note"] = (
                "Activity rates and the creation sparkline reflect only the last "
                f"{window_txt} of events retained in the in-memory trace buffer; the "
                "selected range is wider than buffer retention. Enable the durable "
                "ClickHouse trace store (EB_CLICKHOUSE_ENABLED=true) for full-range "
                "analytics."
            )
    return resp


# ---------------------------------------------------------------------------
# Memory — knowledge-graph explorer (Obsidian-style)
# ---------------------------------------------------------------------------

# Content labels that carry ``gateway_id`` and are safe to expose in the graph.
# Org/Team DataPoints intentionally carry no gateway_id (TD-66) and are excluded
# — a $gw filter would match 0 rows and there is no cross-gateway aggregation.
_GRAPH_ALLOWED_LABELS: tuple[str, ...] = (
    "FactDataPoint",
    "ActorDataPoint",
    "GoalDataPoint",
    "ArtifactDataPoint",
    "ProcedureDataPoint",
)
_GRAPH_MAX_DEPTH = 3
_GRAPH_NODE_CAP = 2000

# Curated scalar projection keys placed under GraphNode.properties (None-dropped).
_GRAPH_NODE_PROP_KEYS: tuple[str, ...] = (
    "scope",
    "memory_class",
    "category",
    "confidence",
    "status",
    "actor_type",
    "authority_level",
    "source_actor_id",
    "archived",
    "created_at_ms",
)


def _resolve_graph_labels(node_types: str | None) -> list[str]:
    """Split/strip a CSV of node labels, intersect against the allowed content set.

    Empty/invalid input (including any Org/Team labels, which are rejected)
    falls back to the full allowed set.
    """
    if not node_types:
        return list(_GRAPH_ALLOWED_LABELS)
    requested = [t.strip() for t in node_types.split(",") if t.strip()]
    allowed = [t for t in requested if t in _GRAPH_ALLOWED_LABELS]
    return allowed or list(_GRAPH_ALLOWED_LABELS)


def _graph_node_from_row(row: dict) -> GraphNode:
    """Build a GraphNode from a curated Cypher projection row, dropping None props."""
    props = {k: row[k] for k in _GRAPH_NODE_PROP_KEYS if row.get(k) is not None}
    return GraphNode(
        id=str(row.get("id") or ""),
        type=str(row.get("type") or ""),
        label=str(row.get("label") if row.get("label") is not None else (row.get("id") or "")),
        properties=props,
    )


def _graph_edges_from_rows(rows: list) -> list[GraphEdge]:
    """Build GraphEdges from source/target/relation_type rows, dropping incomplete ones."""
    edges: list[GraphEdge] = []
    for e in rows or []:
        src = e.get("source")
        tgt = e.get("target")
        rel = e.get("relation_type")
        if not src or not tgt or not rel:
            continue
        edges.append(GraphEdge(source=str(src), target=str(tgt), relation_type=str(rel)))
    return edges


@router.get("/memory/graph", dependencies=[READ])
async def memory_graph(
    request: Request,
    center_id: str | None = None,
    depth: int = Query(1, ge=1, le=3),
    node_types: str | None = None,
    max_nodes: int = Query(300, ge=1, le=_GRAPH_NODE_CAP),
):
    """Gateway-scoped knowledge subgraph for the Obsidian-style graph explorer.

    Two modes:

    * **Mode A** (no ``center_id``) — whole-gateway capped subgraph via two
      queries: Q1 fetches up to ``max_nodes`` content nodes (newest first), Q2
      fetches the directed edges *among* those returned ids. Both endpoints are
      ``$gw``-scoped so there are no dangling edges and no cross-gateway leak.
    * **Mode B** (``center_id`` + ``depth``) — a BFS neighborhood around an
      in-gateway node. The variable-length range bound cannot be a Cypher param
      (Neo4j requires an int literal), so ``depth`` is clamped to
      ``MAX_DEPTH=3`` and %-interpolated as a validated int exactly like
      ``GraphAdapter.query_subgraph``; ``$gw``/``$center_id``/``$labels``/
      ``$max_nodes`` stay bound. The per-hop ``all(x IN nodes(path) WHERE
      x.gateway_id = $gw)`` predicate drops any path crossing into another
      tenant. A center id not present in this gateway yields empty rows → an
      empty graph (effectively 404/empty).

    ``gateway_id`` is read only from ``request.state`` (never from the client).
    On graph outage ``_cypher`` returns ``[]`` → empty graph.
    """
    container = get_container(request)
    gw = _gateway_id(request)
    labels = _resolve_graph_labels(node_types)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    if not center_id:
        # MODE A — whole-gateway capped subgraph (2 queries).
        node_rows = await _cypher(
            container,
            "MATCH (n) WHERE n.gateway_id = $gw "
            "AND any(l IN labels(n) WHERE l IN $labels) "
            "RETURN n.eb_id AS id, head([l IN labels(n) WHERE l IN $labels]) AS type, "
            "coalesce(n.display_name, n.title, n.name, left(n.text, 80), n.eb_id) AS label, "
            "n.scope AS scope, n.memory_class AS memory_class, n.category AS category, "
            "n.confidence AS confidence, n.status AS status, n.actor_type AS actor_type, "
            "n.authority_level AS authority_level, n.source_actor_id AS source_actor_id, "
            "n.archived AS archived, n.eb_created_at AS created_at_ms "
            "ORDER BY coalesce(n.eb_created_at, 0) DESC LIMIT $max_nodes",
            {"gw": gw, "labels": labels, "max_nodes": max_nodes},
        )
        nodes = [_graph_node_from_row(r) for r in node_rows]
        ids = [n.id for n in nodes if n.id]
        if ids:
            edge_rows = await _cypher(
                container,
                "MATCH (a)-[r]->(b) "
                "WHERE a.gateway_id = $gw AND b.gateway_id = $gw "
                "AND a.eb_id IN $ids AND b.eb_id IN $ids "
                "RETURN a.eb_id AS source, b.eb_id AS target, type(r) AS relation_type",
                {"gw": gw, "ids": ids},
            )
            edges = _graph_edges_from_rows(edge_rows)
    else:
        # MODE B — BFS neighborhood. Range bound is a validated int literal
        # (Cypher forbids parameterizing range bounds); everything else bound.
        d = min(max(int(depth), 1), _GRAPH_MAX_DEPTH)
        query = (
            "MATCH path=(c {eb_id: $center_id, gateway_id: $gw})-[*1..%(depth)d]-(m) "
            "WHERE all(x IN nodes(path) WHERE x.gateway_id = $gw) "
            "AND all(x IN nodes(path) WHERE any(l IN labels(x) WHERE l IN $labels)) "
            "UNWIND nodes(path) AS n "
            "WITH collect(DISTINCT {id: n.eb_id, type: head([l IN labels(n) WHERE l IN $labels]), "
            "label: coalesce(n.display_name, n.title, n.name, left(n.text, 80), n.eb_id), "
            "scope: n.scope, memory_class: n.memory_class, category: n.category, "
            "confidence: n.confidence, status: n.status, actor_type: n.actor_type, "
            "authority_level: n.authority_level, source_actor_id: n.source_actor_id, "
            "archived: n.archived, created_at_ms: n.eb_created_at}) AS nodes, "
            "collect(relationships(path)) AS rels_nested "
            "UNWIND rels_nested AS rels UNWIND rels AS rel "
            "WITH nodes, collect(DISTINCT {source: startNode(rel).eb_id, "
            "target: endNode(rel).eb_id, relation_type: type(rel)}) AS edges "
            "RETURN nodes[0..$max_nodes] AS nodes, edges"
        ) % {"depth": d}
        rows = await _cypher(
            container,
            query,
            {"gw": gw, "center_id": center_id, "labels": labels, "max_nodes": max_nodes},
        )
        if rows:
            nodes = [_graph_node_from_row(r) for r in (rows[0].get("nodes") or [])]
            edges = _graph_edges_from_rows(rows[0].get("edges") or [])

    # Drop dangling edges: when Mode B's node slice (nodes[0..$max_nodes]) trims
    # the neighborhood, some collected edges may reference sliced-away nodes.
    # Keep only edges whose BOTH endpoints survive in the returned node set.
    # (No-op for Mode A, whose edge query is already scoped to the returned ids.)
    kept = {n.id for n in nodes if n.id}
    edges = [e for e in edges if e.source in kept and e.target in kept]

    return KnowledgeGraphResponse(
        nodes=nodes,
        edges=edges,
        truncated=len(nodes) >= max_nodes,
        node_count=len(nodes),
        edge_count=len(edges),
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions/active", dependencies=[READ])
async def sessions_active(request: Request):
    """Active session keys (Redis SET) enriched with a trace summary."""
    container = get_container(request)
    keys = get_redis_keys(request)
    redis = _get_redis(container)
    gw = _gateway_id(request)

    session_keys: list[str] = []
    if keys is not None:
        session_keys = await _smembers(redis, keys.active_sessions())

    # Enrich each active session with a lightweight event summary.
    ledger = get_trace_ledger(request)
    summaries: dict[str, dict] = {}
    if ledger is not None and session_keys:
        try:
            result = await ledger.list_sessions(gateway_id=gw, limit=1000)
            for s in result.sessions:
                summaries[s.session_key] = {
                    "session_id": str(s.session_id),
                    "event_count": s.event_count,
                    "last_event_at": s.last_event_at,
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: list_sessions failed: %s", exc)

    out = []
    for sk in sorted(session_keys):
        meta = summaries.get(sk, {})
        out.append(
            ActiveSessionSummary(
                session_key=sk,
                session_id=meta.get("session_id"),
                event_count=int(meta.get("event_count", 0) or 0),
                last_event_at=meta.get("last_event_at"),
            ).model_dump(mode="json")
        )
    return {"sessions": out}


@router.get("/sessions/recent", dependencies=[READ])
async def sessions_recent(
    request: Request,
    time_range: str = Query("24h"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Recently active sessions from the trace stream (session boundaries)."""
    ledger = get_trace_ledger(request)
    gw = _gateway_id(request)
    tr, since = _range_start(time_range)
    if ledger is None:
        return {"time_range": tr, "sessions": []}
    try:
        result = await ledger.list_sessions(gateway_id=gw, limit=limit)
        sessions = [
            s.model_dump(mode="json")
            for s in result.sessions
            if s.last_event_at >= since
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: recent sessions failed: %s", exc)
        sessions = []
    return {"time_range": tr, "sessions": sessions}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@router.get("/guards/activity", dependencies=[READ])
async def guards_activity(request: Request, time_range: str = Query("24h")):
    """Cross-session guard activity aggregate for a time window."""
    ledger = get_trace_ledger(request)
    gw = _gateway_id(request)
    tr, since = _range_start(time_range)

    triggers = 0
    near_misses = 0
    by_outcome: dict[str, int] = {}
    recent: list[dict] = []
    if ledger is not None:
        from elephantbroker.schemas.trace import TraceQuery

        try:
            events = await ledger.query_trace(
                TraceQuery(gateway_id=gw, from_timestamp=since, limit=10000)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: guards activity query_trace failed: %s", exc)
            events = []
        guard_events = [
            e for e in events
            if e.event_type.value in ("guard_triggered", "guard_near_miss")
        ]
        for e in guard_events:
            if e.event_type.value == "guard_triggered":
                triggers += 1
            else:
                near_misses += 1
            outcome = str((e.payload or {}).get("outcome", e.event_type.value))
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        for e in sorted(guard_events, key=lambda x: x.timestamp, reverse=True)[:20]:
            recent.append({
                "id": str(e.id),
                "event_type": e.event_type.value,
                "timestamp": e.timestamp.isoformat(),
                "session_key": e.session_key,
                "payload": e.payload or {},
            })

    return GuardActivityResponse(
        time_range=tr,
        triggers=triggers,
        near_misses=near_misses,
        by_outcome=by_outcome,
        recent_events=recent,
    ).model_dump(mode="json")


@router.get("/guards/events", dependencies=[READ])
async def guards_events(
    request: Request,
    time_range: str = Query("24h"),
    limit: int = Query(200, ge=1, le=10000),
):
    """Raw cross-session guard trace events for the given time window."""
    ledger = get_trace_ledger(request)
    gw = _gateway_id(request)
    tr, since = _range_start(time_range)
    if ledger is None:
        return {"time_range": tr, "events": []}
    from elephantbroker.schemas.trace import TraceQuery

    try:
        events = await ledger.query_trace(
            TraceQuery(gateway_id=gw, from_timestamp=since, limit=10000)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: guards events query_trace failed: %s", exc)
        events = []
    guard = [
        e.model_dump(mode="json")
        for e in events
        if e.event_type.value in ("guard_triggered", "guard_near_miss")
    ]
    return {"time_range": tr, "events": guard[:limit]}


@router.get("/guards/rules", dependencies=[READ])
async def guards_rules(request: Request, enabled_only: bool = False):
    """All guard rules: builtin + custom (from CustomRuleStore)."""
    container = get_container(request)
    gw = _gateway_id(request)

    merged: dict[str, StaticRule] = {}
    # Builtins (lowest priority)
    try:
        from elephantbroker.runtime.guards.rules import StaticRuleRegistry

        for rule in StaticRuleRegistry().get_builtin_rules():
            merged[rule.id] = rule
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: builtin rules unavailable: %s", exc)

    # Custom rules (override builtins by id)
    store = _get_custom_rule_store(container)
    if store is not None:
        try:
            for rule in await store.list_rules(gateway_id=gw, enabled_only=enabled_only):
                merged[rule.id] = rule
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: custom rules list failed: %s", exc)

    rules = list(merged.values())
    if enabled_only:
        rules = [r for r in rules if r.enabled]
    return {"rules": [r.model_dump(mode="json") for r in rules]}


@router.post("/guards/rules", dependencies=[WRITE])
async def guards_create_rule(body: StaticRule, request: Request):
    """Create a custom guard rule (persisted in CustomRuleStore)."""
    container = get_container(request)
    store = _get_custom_rule_store(container)
    if store is None:
        return JSONResponse(status_code=503, content={"detail": "Custom rule store not available"})
    gw = _gateway_id(request)
    body.source = "custom"
    try:
        created = await store.create_rule(gateway_id=gw, rule=body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: create rule failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": f"Failed to create rule: {exc}"})
    _invalidate_guard_rules_probe(container)
    return created.model_dump(mode="json")


@router.put("/guards/rules/{rule_id}", dependencies=[WRITE])
async def guards_update_rule(rule_id: str, body: GuardRuleUpdate, request: Request):
    """Update a custom guard rule (whitelisted fields only)."""
    container = get_container(request)
    store = _get_custom_rule_store(container)
    if store is None:
        return JSONResponse(status_code=503, content={"detail": "Custom rule store not available"})
    gw = _gateway_id(request)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return JSONResponse(status_code=422, content={"detail": "No updatable fields provided"})
    try:
        updated = await store.update_rule(gateway_id=gw, rule_id=rule_id, updates=updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: update rule failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": f"Failed to update rule: {exc}"})
    if updated is None:
        return JSONResponse(status_code=404, content={"detail": "Rule not found"})
    _invalidate_guard_rules_probe(container)
    return updated.model_dump(mode="json")


@router.delete("/guards/rules/{rule_id}", dependencies=[WRITE])
async def guards_delete_rule(rule_id: str, request: Request):
    """Delete a custom guard rule."""
    container = get_container(request)
    store = _get_custom_rule_store(container)
    if store is None:
        return JSONResponse(status_code=503, content={"detail": "Custom rule store not available"})
    gw = _gateway_id(request)
    try:
        ok = await store.delete_rule(gateway_id=gw, rule_id=rule_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: delete rule failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": f"Failed to delete rule: {exc}"})
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "Rule not found"})
    _invalidate_guard_rules_probe(container)
    return {"rule_id": rule_id, "status": "deleted"}


@router.get("/guards/approvals/pending", dependencies=[READ])
async def guards_pending_approvals(request: Request):
    """Cross-session pending approvals (Redis SET), hydrated best-effort."""
    container = get_container(request)
    keys = get_redis_keys(request)
    redis = _get_redis(container)
    if keys is None:
        return {"pending": []}
    request_ids = await _smembers(redis, keys.pending_approvals())

    engine = get_guard_engine(request)
    hydrated: list[dict] = []
    approvals = getattr(engine, "_approvals", None) if engine is not None else None
    for rid in sorted(request_ids):
        record = None
        if approvals is not None:
            try:
                record = await approvals.get(uuid.UUID(rid), "")
            except Exception:  # noqa: BLE001
                record = None
        if record is not None:
            hydrated.append(record.model_dump(mode="json"))
        else:
            # Self-heal (review #1/#3): the record is gone — resolved with a
            # drain failure, TTL-expired, or a create-time partial write — but
            # its id still lingers in the pending SET. The SET has no TTL/reaper
            # of its own, so drop the orphan here (SREM + del reverse index)
            # instead of rendering a permanent phantom stub. Best-effort.
            try:
                await redis.srem(keys.pending_approvals(), rid)
                await redis.delete(keys.approval_agent(rid))
            except Exception:  # noqa: BLE001 - self-heal is best-effort
                pass
    return {"pending": hydrated}


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.get("/goals", dependencies=[READ])
async def goals(request: Request):
    """Root persistent goals (parent_goal_id IS NULL) for this gateway."""
    container = get_container(request)
    gw = _gateway_id(request)
    rows = await _cypher(
        container,
        "MATCH (g:GoalDataPoint) WHERE g.gateway_id = $gw AND g.parent_goal_id IS NULL "
        "RETURN properties(g) AS props",
        {"gw": gw},
    )
    out = []
    for r in rows:
        p = r.get("props") or {}
        blockers = p.get("blockers") or []
        out.append(
            GoalSummary(
                goal_id=str(p.get("eb_id") or p.get("id") or ""),
                title=str(p.get("title", "")),
                status=str(p.get("status", "")),
                scope=str(p.get("scope", "")),
                confidence=float(p.get("confidence", 0.0) or 0.0),
                blockers=list(blockers) if isinstance(blockers, list) else [],
                org_id=p.get("org_id"),
                team_id=p.get("team_id"),
            ).model_dump(mode="json")
        )
    return {"goals": out}


# ---------------------------------------------------------------------------
# Procedures
# ---------------------------------------------------------------------------


@router.get("/procedures", dependencies=[READ])
async def procedures(request: Request):
    """Procedure list with best-effort execution counts."""
    container = get_container(request)
    gw = _gateway_id(request)
    rows = await _cypher(
        container,
        "MATCH (p:ProcedureDataPoint) WHERE p.gateway_id = $gw "
        "RETURN properties(p) AS props",
        {"gw": gw},
    )
    out = []
    for r in rows:
        p = r.get("props") or {}
        out.append(
            ProcedureSummary(
                procedure_id=str(p.get("eb_id") or p.get("id") or ""),
                name=str(p.get("name", "")),
                description=str(p.get("description", "")),
                scope=str(p.get("scope", "")),
                execution_count=0,  # cross-session execution counts require per-session scan
            ).model_dump(mode="json")
        )
    return {"procedures": out}


@router.get("/procedures/{procedure_id}/detail", dependencies=[READ])
async def procedure_detail(procedure_id: uuid.UUID, request: Request):
    """Procedure definition + steps + active executions (best-effort)."""
    container = get_container(request)
    gw = _gateway_id(request)
    rows = await _cypher(
        container,
        "MATCH (p:ProcedureDataPoint {eb_id: $pid, gateway_id: $gw}) "
        "RETURN properties(p) AS props",
        {"pid": str(procedure_id), "gw": gw},
    )
    if not rows:
        return JSONResponse(status_code=404, content={"detail": "Procedure not found"})
    p = rows[0].get("props") or {}

    steps: list[dict] = []
    steps_json = p.get("steps_json")
    if steps_json:
        try:
            import json as _json

            parsed = _json.loads(steps_json)
            if isinstance(parsed, list):
                steps = parsed
        except Exception:  # noqa: BLE001
            steps = []

    summary = ProcedureSummary(
        procedure_id=str(p.get("eb_id") or procedure_id),
        name=str(p.get("name", "")),
        description=str(p.get("description", "")),
        scope=str(p.get("scope", "")),
        execution_count=0,
    )
    return ProcedureDetailResponse(
        procedure=summary,
        steps=steps,
        active_execution_ids=[],
        audit_trail=[],
        note="Active executions and audit trail are session-scoped; use the session views for live runs.",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------


@router.get("/actors", dependencies=[READ])
async def actors(
    request: Request,
    actor_type: str | None = None,
    status: str = Query("all"),
):
    """Actor roster enriched with owned-fact counts + last-active time.

    ``status`` selects which actors are returned: ``active`` (default-active +
    unset), ``inactive`` (deactivated), or ``all``. It defaults to ``all`` so the
    grid's client-side status filter has inactive actors to show — the old query
    hard-filtered to active-only, so deactivated actors vanished entirely and the
    Inactive/All filters could never populate (actors-orgs-3).
    """
    container = get_container(request)
    gw = _gateway_id(request)
    params: dict = {"gw": gw}
    status_l = (status or "all").lower()
    if status_l == "inactive":
        active_clause = "AND a.active = false "
    elif status_l == "all":
        active_clause = ""
    else:  # "active" (and any unrecognised value) -> default-active semantics
        active_clause = "AND (a.active = true OR a.active IS NULL) "
    type_clause = ""
    if actor_type:
        type_clause = "AND a.actor_type = $atype "
        params["atype"] = actor_type
    rows = await _cypher(
        container,
        "MATCH (a:ActorDataPoint) WHERE a.gateway_id = $gw "
        + active_clause + type_clause +
        "OPTIONAL MATCH (f:FactDataPoint {gateway_id: $gw}) "
        "WHERE f.source_actor_id = a.eb_id "
        "RETURN properties(a) AS props, count(f) AS fact_count, "
        "max(f.eb_created_at) AS last_active_ms",
        params,
    )
    out = []
    for r in rows:
        p = r.get("props") or {}
        handles = p.get("handles") or []
        row = ActorSummary(
            actor_id=str(p.get("eb_id") or p.get("id") or ""),
            display_name=str(p.get("display_name", "")),
            actor_type=str(p.get("actor_type", "")),
            authority_level=int(p.get("authority_level", 0) or 0),
            org_id=p.get("org_id"),
            active=bool(p.get("active", True)),
            fact_count=int(r.get("fact_count", 0) or 0),
            handles=list(handles) if isinstance(handles, list) else [],
        ).model_dump(mode="json")
        # last_active is not on the ActorSummary schema (see crossFileNeeds);
        # attach it to the row so the grid's Last-active column can populate from
        # the newest owned fact instead of always rendering an em-dash
        # (actors-orgs-5). ``eb_created_at`` is the stored epoch-ms key — the old
        # detail query read a nonexistent ``created_at`` and got null.
        la = _epoch_ms_to_dt(r.get("last_active_ms"))
        row["last_active"] = la.isoformat() if la else None
        out.append(row)
    return {"actors": out}


@router.get("/actors/{actor_id}/detail", dependencies=[READ])
async def actor_detail(actor_id: uuid.UUID, request: Request):
    """Actor identity + owned-fact count + teams + org."""
    container = get_container(request)
    gw = _gateway_id(request)
    rows = await _cypher(
        container,
        "MATCH (a:ActorDataPoint {eb_id: $aid, gateway_id: $gw}) "
        "OPTIONAL MATCH (f:FactDataPoint {gateway_id: $gw}) "
        "WHERE f.source_actor_id = a.eb_id "
        "WITH a, count(DISTINCT f) AS fact_count, max(f.eb_created_at) AS last_active_ms "
        "OPTIONAL MATCH (a)-[:OWNS_GOAL]->(g:GoalDataPoint) WHERE g.gateway_id = $gw "
        "RETURN properties(a) AS props, fact_count, last_active_ms, "
        "count(DISTINCT g) AS goals_owned",
        {"aid": str(actor_id), "gw": gw},
    )
    if not rows or not rows[0].get("props"):
        return JSONResponse(status_code=404, content={"detail": "Actor not found"})
    p = rows[0].get("props") or {}
    handles = p.get("handles") or []
    team_ids = p.get("team_ids") or []
    fact_count = int(rows[0].get("fact_count", 0) or 0)
    goals_owned = int(rows[0].get("goals_owned", 0) or 0)

    # Populate last_active from the newest owned fact's stored epoch-ms key.
    # The old query read ``max(f.created_at)`` — a property FactDataPoint never
    # writes (it stores ``eb_created_at``) — so last_active was always null and
    # the UI rendered an em-dash (actors-orgs-5).
    last_active = _epoch_ms_to_dt(rows[0].get("last_active_ms"))

    summary = ActorSummary(
        actor_id=str(p.get("eb_id") or actor_id),
        display_name=str(p.get("display_name", "")),
        actor_type=str(p.get("actor_type", "")),
        authority_level=int(p.get("authority_level", 0) or 0),
        org_id=p.get("org_id"),
        active=bool(p.get("active", True)),
        fact_count=fact_count,
        handles=list(handles) if isinstance(handles, list) else [],
    )
    detail = ActorDetailResponse(
        actor=summary,
        team_ids=[str(t) for t in team_ids] if isinstance(team_ids, list) else [],
        org_id=p.get("org_id"),
        fact_count=fact_count,
        last_active=last_active,
    ).model_dump(mode="json")
    # goals_owned is not on the ActorDetailResponse schema (see crossFileNeeds);
    # the actor-detail page reads it to replace the hardcoded "Owns 0 goals"
    # (actors-orgs-7). Owner->goal is the ``(actor)-[:OWNS_GOAL]->(goal)`` edge
    # the goal manager writes (goals/manager.py) — the ``owner_actor_ids`` list
    # property is JSON-serialised and not Cypher list-membership queryable.
    detail["goals_owned"] = goals_owned
    return detail


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


@router.get("/organizations", dependencies=[READ])
async def organizations(request: Request):
    """Org list with team + actor counts."""
    container = get_container(request)
    gw = _gateway_id(request)
    rows = await _cypher(
        container,
        "MATCH (o:OrganizationDataPoint) "
        "OPTIONAL MATCH (t:TeamDataPoint)-[:BELONGS_TO]->(o) "
        "WITH o, count(DISTINCT t) AS team_count "
        "OPTIONAL MATCH (a:ActorDataPoint {gateway_id: $gw}) WHERE a.org_id = o.eb_id "
        "RETURN properties(o) AS props, team_count, count(DISTINCT a) AS actor_count",
        {"gw": gw},
    )
    store = getattr(container, "org_override_store", None)
    out = []
    for r in rows:
        p = r.get("props") or {}
        org_id = str(p.get("eb_id") or "")
        org = OrganizationSummary(
            org_id=org_id,
            name=str(p.get("name", "")),
            display_label=str(p.get("display_label", "")),
            team_count=int(r.get("team_count", 0) or 0),
            actor_count=int(r.get("actor_count", 0) or 0),
        ).model_dump(mode="json")
        # has_profile_override drives the org "Profile" column chip (custom vs
        # default). The org node stores no profile — the association lives in the
        # OrgOverrideStore, so the column was always blank (actors-orgs-9). Not on
        # OrganizationSummary (see crossFileNeeds); attached to the row.
        has_override = False
        if store is not None and org_id:
            try:
                overrides = await store.list_overrides(org_id)
                has_override = bool(overrides)
            except Exception as exc:  # noqa: BLE001
                logger.warning("dashboard: org override lookup failed: %s", exc)
        org["has_profile_override"] = has_override
        out.append(org)
    return {"organizations": out}


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@router.get("/profiles", dependencies=[READ])
async def profiles(request: Request):
    """Profile list with inheritance + best-effort active-session counts.

    ``session_count`` was hardcoded to 0 and name/inheritance omitted
    (guards-profiles-8). We resolve each profile for its real ``name``/``extends``
    and count distinct sessions that resolved it from the retained trace window.
    The count is bounded by in-memory trace-buffer retention (RC-5) — it reflects
    recently observed sessions, not an all-time total.
    """
    registry = get_profile_registry(request)
    if registry is None:
        return {"profiles": []}
    try:
        names = await registry.list_profiles()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: list_profiles failed: %s", exc)
        names = []

    # Best-effort per-profile session counts from the trace stream: a session
    # resolves its profile at bootstrap (bootstrap_completed.payload.profile_name)
    # or via profile_resolved.payload.profile.
    session_counts: dict[str, set[str]] = {}
    ledger = get_trace_ledger(request)
    gw = _gateway_id(request)
    if ledger is not None:
        from elephantbroker.schemas.trace import TraceQuery

        try:
            events = await ledger.query_trace(TraceQuery(gateway_id=gw, limit=10000))
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: profiles trace query failed: %s", exc)
            events = []
        for e in events:
            if e.event_type.value not in ("bootstrap_completed", "profile_resolved"):
                continue
            payload = e.payload or {}
            pname = payload.get("profile_name") or payload.get("profile")
            sess = e.session_key or (str(e.session_id) if e.session_id else None)
            if pname and sess:
                session_counts.setdefault(str(pname), set()).add(sess)

    out = []
    for n in names:
        name = n
        extends = None
        try:
            policy = await registry.resolve_profile(n)
            name = policy.name or n
            extends = policy.extends
        except Exception:  # noqa: BLE001 - resolve is best-effort enrichment
            pass
        row = ProfileSummary(
            profile_id=n,
            session_count=len(session_counts.get(n, ())),
        ).model_dump(mode="json")
        # Additive metadata (safe for existing consumers): human name +
        # inheritance parent so the row is self-describing without a second
        # /profiles/{name}/resolve round-trip.
        row["name"] = name
        row["extends"] = extends
        out.append(row)
    return {"profiles": out}


# ---------------------------------------------------------------------------
# Preferences & saved views
# ---------------------------------------------------------------------------


@router.get("/preferences", dependencies=[READ])
async def get_preferences(request: Request):
    """Return the calling actor's dashboard preferences (defaults if none)."""
    container = get_container(request)
    actor_id = _actor_id(request)
    store = _get_prefs_store(container)
    if store is not None and hasattr(store, "get_preferences"):
        try:
            prefs = await store.get_preferences(actor_id)
            if prefs is not None:
                if isinstance(prefs, UserPreferences):
                    return prefs.model_dump(mode="json")
                if isinstance(prefs, dict):
                    return UserPreferences(actor_id=actor_id, **{
                        k: v for k, v in prefs.items()
                        if k in UserPreferences.model_fields
                    }).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: get_preferences failed: %s", exc)
    return UserPreferences(actor_id=actor_id).model_dump(mode="json")


@router.put("/preferences", dependencies=[READ])
async def update_preferences(body: UserPreferences, request: Request):
    """Persist the calling actor's dashboard preferences."""
    container = get_container(request)
    actor_id = _actor_id(request)
    body.actor_id = actor_id  # scope to caller; never trust body actor_id
    store = _get_prefs_store(container)
    if store is not None:
        setter = getattr(store, "set_preferences", None) or getattr(store, "update_preferences", None)
        if setter is not None:
            try:
                await setter(actor_id, body.model_dump(mode="json"))
            except TypeError:
                try:
                    await setter(body)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("dashboard: set_preferences failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("dashboard: set_preferences failed: %s", exc)
    return body.model_dump(mode="json")


@router.get("/saved-views", dependencies=[READ])
async def list_saved_views(request: Request, resource: str | None = None):
    """List the calling actor's saved views, optionally filtered by resource."""
    container = get_container(request)
    actor_id = _actor_id(request)
    store = _get_prefs_store(container)
    if store is None or not hasattr(store, "list_saved_views"):
        return {"views": []}
    try:
        views = await store.list_saved_views(actor_id, resource)
    except TypeError:
        try:
            views = await store.list_saved_views(actor_id=actor_id, resource=resource)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: list_saved_views failed: %s", exc)
            views = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: list_saved_views failed: %s", exc)
        views = []
    out = []
    for v in views or []:
        if isinstance(v, SavedView):
            out.append(v.model_dump(mode="json"))
        elif isinstance(v, dict):
            out.append(v)
    return {"views": out}


@router.post("/saved-views", dependencies=[READ])
async def create_saved_view(body: SavedViewCreate, request: Request):
    """Create a saved filter/sort view for the calling actor."""
    container = get_container(request)
    actor_id = _actor_id(request)
    store = _get_prefs_store(container)
    view = SavedView(
        id=str(uuid.uuid4()),
        actor_id=actor_id,
        name=body.name,
        resource=body.resource,
        filters=body.filters,
        sort=body.sort,
        created_at=datetime.now(UTC),
    )
    if store is None or not hasattr(store, "create_saved_view"):
        return JSONResponse(status_code=503, content={"detail": "Preferences store not available"})
    try:
        created = await store.create_saved_view(actor_id, view.model_dump(mode="json"))
        if isinstance(created, SavedView):
            return created.model_dump(mode="json")
        if isinstance(created, dict):
            return created
    except TypeError:
        try:
            await store.create_saved_view(view)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: create_saved_view failed: %s", exc)
            return JSONResponse(status_code=400, content={"detail": "Failed to create view"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: create_saved_view failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": "Failed to create view"})
    return view.model_dump(mode="json")


@router.delete("/saved-views/{view_id}", dependencies=[READ])
async def delete_saved_view(view_id: str, request: Request):
    """Delete a saved view owned by the calling actor."""
    container = get_container(request)
    actor_id = _actor_id(request)
    store = _get_prefs_store(container)
    if store is None or not hasattr(store, "delete_saved_view"):
        return JSONResponse(status_code=503, content={"detail": "Preferences store not available"})
    try:
        ok = await store.delete_saved_view(view_id, actor_id)
    except TypeError:
        try:
            ok = await store.delete_saved_view(view_id=view_id, actor_id=actor_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard: delete_saved_view failed: %s", exc)
            ok = False
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard: delete_saved_view failed: %s", exc)
        ok = False
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "View not found"})
    return {"view_id": view_id, "status": "deleted"}
