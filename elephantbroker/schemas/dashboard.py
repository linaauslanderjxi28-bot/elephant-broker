"""Dashboard API response/request schemas (Phase 11 — §11.2).

These models shape the aggregate views consumed by the Refine dashboard data
provider. They are read-heavy projections built by ``api/routes/dashboard.py``
from Neo4j (current state), the in-memory TraceLedger (event stream), and Redis
(active sessions / pending approvals). None of these models own persistence —
they are transport DTOs only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from elephantbroker.schemas.base import Scope
from elephantbroker.schemas.fact import FactAssertion, FactCategory, MemoryClass

# ---------------------------------------------------------------------------
# Overview & system
# ---------------------------------------------------------------------------


class ComponentHealth(BaseModel):
    """Health of a single infrastructure component (neo4j, qdrant, redis, ...)."""

    status: str  # "ok" | "error" | "not configured"
    latency_ms: float | None = None


class RecentEvent(BaseModel):
    """A pre-formatted recent trace event for the overview activity feed."""

    timestamp: datetime
    summary: str  # human-readable, computed server-side
    event_type: str  # raw type for chip color-coding
    session_key: str | None = None


class DashboardOverview(BaseModel):
    """Aggregate landing-page view (counts + health + recent activity)."""

    time_range: str  # "1h" | "6h" | "24h" | "7d"

    # Fact stats
    total_facts: int = 0
    facts_in_period: int = 0
    facts_by_class: dict[str, int] = Field(default_factory=dict)
    facts_by_scope: dict[str, int] = Field(default_factory=dict)

    # Session stats
    active_sessions: int = 0

    # Entity counts
    total_actors: int = 0
    total_organizations: int = 0
    total_goals_active: int = 0

    # Guard stats
    guard_triggers_in_period: int = 0
    guard_near_misses_in_period: int = 0

    # Error stats
    errors_in_period: int = 0

    # System health
    system_health: str = "healthy"  # "healthy" | "degraded" | "unhealthy"
    components: dict[str, ComponentHealth] = Field(default_factory=dict)

    # Recent activity (last 10)
    recent_events: list[RecentEvent] = Field(default_factory=list)


class GatewayInfo(BaseModel):
    """A single available gateway."""

    gateway_id: str
    org_id: str | None = None
    is_current: bool = False


# ---------------------------------------------------------------------------
# Memory — browse / detail / stats
# ---------------------------------------------------------------------------


class MemoryBrowseRequest(BaseModel):
    """Filter/sort/paginate request for the memory browse DataGrid."""

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)
    scope: Scope | None = None
    memory_class: MemoryClass | None = None
    category: FactCategory | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_actor_id: uuid.UUID | None = None
    session_key: str | None = None
    goal_id: uuid.UUID | None = None
    text_contains: str | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


class FactEdge(BaseModel):
    """A single graph edge from/to a fact, with resolved target display name."""

    relation_type: str
    direction: str  # "outgoing" | "incoming"
    target_id: str | None = None
    target_type: str | None = None
    target_label: str = ""
    target_properties: dict = Field(default_factory=dict)


class LinkedClaim(BaseModel):
    """A claim this fact supports as evidence."""

    claim_id: str
    claim_text: str = ""
    status: str = ""
    evidence_count: int = 0


class FactUsageSummary(BaseModel):
    """Computed usage statistics for display."""

    use_count: int = 0
    successful_use_count: int = 0
    success_rate: float = 0.0  # successful / max(use, 1) * 100
    last_used_at: datetime | None = None
    superseded_by: str | None = None
    goal_relevance_tags: dict[str, str] = Field(default_factory=dict)


class FactDetailResponse(BaseModel):
    """Full detail view for a single fact: fact + edges + claims + usage."""

    fact: FactAssertion
    edges: list[FactEdge] = Field(default_factory=list)
    claims: list[LinkedClaim] = Field(default_factory=list)
    usage: FactUsageSummary
    session_key: str | None = None
    extraction_trace_event_id: uuid.UUID | None = None


class ActorFactCount(BaseModel):
    """Actor ranked by owned fact count (stats page)."""

    actor_id: str
    actor_label: str = ""
    fact_count: int = 0


class TimeBucket(BaseModel):
    """A single hourly bucket for the creation sparkline."""

    timestamp: datetime
    count: int = 0


class MemoryStatsResponse(BaseModel):
    """Memory health dashboard — current shape + activity rates."""

    time_range: str

    # Current state (Neo4j)
    total_facts: int = 0
    by_class: dict[str, int] = Field(default_factory=dict)
    by_scope: dict[str, int] = Field(default_factory=dict)
    avg_confidence: float = 0.0
    avg_use_count: float = 0.0
    avg_success_rate: float = 0.0
    top_actors: list[ActorFactCount] = Field(default_factory=list)

    # Activity rates (trace event stream, in time_range)
    extractions_in_period: int = 0
    dedup_rate: float = 0.0
    supersession_rate: float = 0.0

    # Time series (for sparkline chart)
    creation_over_time: list[TimeBucket] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory — knowledge-graph explorer (Obsidian-style)
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    """A single knowledge-graph node projected from a gateway-scoped Cypher row."""

    id: str  # eb_id
    type: str  # labels(n)[0], e.g. "FactDataPoint"
    label: str = ""  # coalesce(display_name, title, name, left(text,80), eb_id)
    properties: dict = Field(default_factory=dict)  # curated scalars: scope,
    # memory_class, category, confidence, status, actor_type, authority_level,
    # source_actor_id, archived, created_at_ms (int epoch-ms). Never blanket
    # properties(n).


class GraphEdge(BaseModel):
    """A directed typed edge between two in-gateway nodes."""

    source: str  # startNode.eb_id
    target: str  # endNode.eb_id
    relation_type: str  # type(r): ABOUT_ACTOR|CREATED_BY|SERVES_GOAL|CHILD_OF|
    # SUPPORTS|MEMBER_OF|OWNED_BY|BELONGS_TO|SUPERSEDES


class KnowledgeGraphResponse(BaseModel):
    """Gateway-scoped subgraph for the Obsidian-style memory graph explorer."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False  # len(nodes) >= max_nodes
    node_count: int = 0
    edge_count: int = 0


# ---------------------------------------------------------------------------
# Actors / organizations / goals / procedures / sessions / profiles
# ---------------------------------------------------------------------------


class ActorSummary(BaseModel):
    """Enriched actor listing row."""

    actor_id: str
    display_name: str = ""
    actor_type: str = ""
    authority_level: int = 0
    org_id: str | None = None
    active: bool = True
    fact_count: int = 0
    handles: list[str] = Field(default_factory=list)


class ActorDetailResponse(BaseModel):
    """Actor detail: identity + stats + teams + org."""

    actor: ActorSummary
    team_ids: list[str] = Field(default_factory=list)
    org_id: str | None = None
    fact_count: int = 0
    last_active: datetime | None = None


class OrganizationSummary(BaseModel):
    """Org listing row with team/actor counts."""

    org_id: str
    name: str = ""
    display_label: str = ""
    team_count: int = 0
    actor_count: int = 0


class GoalSummary(BaseModel):
    """Root goal listing row."""

    goal_id: str
    title: str = ""
    status: str = ""
    scope: str = ""
    confidence: float = 0.0
    blockers: list[str] = Field(default_factory=list)
    org_id: str | None = None
    team_id: str | None = None


class ProcedureSummary(BaseModel):
    """Procedure listing row with execution count."""

    procedure_id: str
    name: str = ""
    description: str = ""
    scope: str = ""
    execution_count: int = 0


class ProcedureDetailResponse(BaseModel):
    """Procedure detail: definition + active executions + audit trail."""

    procedure: ProcedureSummary
    steps: list[dict] = Field(default_factory=list)
    active_execution_ids: list[str] = Field(default_factory=list)
    audit_trail: list[dict] = Field(default_factory=list)
    note: str | None = None


class ActiveSessionSummary(BaseModel):
    """An active session enriched with a lightweight trace summary."""

    session_key: str
    session_id: str | None = None
    event_count: int = 0
    last_event_at: datetime | None = None


class ProfileSummary(BaseModel):
    """Profile listing row with active-session count."""

    profile_id: str
    session_count: int = 0


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class GuardActivityResponse(BaseModel):
    """Cross-session guard activity aggregate for a time window."""

    time_range: str
    triggers: int = 0
    near_misses: int = 0
    by_outcome: dict[str, int] = Field(default_factory=dict)
    recent_events: list[dict] = Field(default_factory=list)


class GuardRuleUpdate(BaseModel):
    """Whitelisted updatable fields for a custom guard rule."""

    model_config = ConfigDict(extra="forbid")

    pattern: str | None = None
    pattern_type: str | None = None
    outcome: str | None = None
    description: str | None = None
    enabled: bool | None = None
    min_approval_authority: int | None = None


# ---------------------------------------------------------------------------
# Preferences & saved views
# ---------------------------------------------------------------------------


class UserPreferences(BaseModel):
    """Per-actor dashboard preferences."""

    actor_id: str | None = None
    default_page: str = "/"
    items_per_page: int = Field(default=50, ge=1, le=200)
    theme: str = "light"
    selected_gateway: str | None = None
    preferences: dict = Field(default_factory=dict)


class SavedView(BaseModel):
    """A persisted filter/sort view for a resource."""

    id: str
    actor_id: str | None = None
    name: str
    resource: str
    filters: dict = Field(default_factory=dict)
    sort: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class SavedViewCreate(BaseModel):
    """Create request for a saved view."""

    name: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    filters: dict = Field(default_factory=dict)
    sort: dict = Field(default_factory=dict)
