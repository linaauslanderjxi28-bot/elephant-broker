"""Unit tests for the Phase 11 (branch EB-FE) schema additions.

Scope: ONLY the fields/models added or changed on this branch (diff main...HEAD
-- elephantbroker/schemas):

  * schemas/dashboard.py (new file) — dashboard transport DTOs.
  * schemas/fact.py       — FactSortField / FactSort / FactFilters / FactPage.
  * schemas/config.py     — DashboardAuthConfig + new AuditConfig db-path fields
                            + ElephantBrokerConfig.dashboard_auth + env bindings.
  * schemas/actor.py      — ActorRef.active (soft-deactivation flag).
  * schemas/trace.py      — TraceEvent.fact_ids.
  * schemas/procedure.py  — ProcedureDefinition.org_id / team_id.

These are pure Pydantic-v2 model tests: construction, defaults, validation
errors, and round-trip model_dump. No I/O — nothing here touches Cognee, Neo4j,
Redis, or the FastAPI container.
"""
import uuid

import pytest
from pydantic import ValidationError

from elephantbroker.schemas.actor import ActorRef, ActorType
from elephantbroker.schemas.base import Scope
from elephantbroker.schemas.config import (
    AuditConfig,
    DashboardAuthConfig,
    ElephantBrokerConfig,
    ENV_OVERRIDE_BINDINGS,
)
from elephantbroker.schemas.dashboard import (
    ActorFactCount,
    ComponentHealth,
    DashboardOverview,
    FactDetailResponse,
    FactUsageSummary,
    GatewayInfo,
    GraphNode,
    GuardRuleUpdate,
    KnowledgeGraphResponse,
    MemoryBrowseRequest,
    MemoryStatsResponse,
    SavedView,
    SavedViewCreate,
    UserPreferences,
)
from elephantbroker.schemas.fact import (
    FactAssertion,
    FactFilters,
    FactPage,
    FactSort,
    FactSortField,
    MemoryClass,
)
from elephantbroker.schemas.procedure import ProcedureDefinition
from elephantbroker.schemas.trace import TraceEvent, TraceEventType


# ---------------------------------------------------------------------------
# fact.py — FactSortField / FactSort / FactFilters / FactPage (NEW)
# ---------------------------------------------------------------------------


class TestFactSortField:
    def test_whitelisted_values(self):
        assert {m.value for m in FactSortField} == {
            "created_at",
            "updated_at",
            "confidence",
            "use_count",
            "last_used_at",
        }

    def test_string_coercion(self):
        assert FactSortField("confidence") is FactSortField.CONFIDENCE

    def test_unknown_column_rejected(self):
        # The enum is the injection-safety boundary — arbitrary column names
        # must not resolve to a member.
        with pytest.raises(ValueError):
            FactSortField("created_at; DROP TABLE facts")


class TestFactSort:
    def test_defaults(self):
        s = FactSort()
        assert s.field is FactSortField.CREATED_AT
        assert s.descending is True

    def test_explicit_field_and_direction(self):
        s = FactSort(field=FactSortField.USE_COUNT, descending=False)
        assert s.field is FactSortField.USE_COUNT
        assert s.descending is False

    def test_invalid_field_rejected(self):
        with pytest.raises(ValidationError):
            FactSort(field="bogus")


class TestFactFilters:
    def test_empty_filters_all_none(self):
        f = FactFilters()
        # Every field optional -> selects all facts in the gateway.
        assert f.scope is None
        assert f.memory_class is None
        assert f.category is None
        assert f.actor_id is None
        assert f.session_key is None
        assert f.session_id is None
        assert f.archived is None
        assert f.min_confidence is None
        assert f.max_confidence is None
        assert f.text_contains is None
        assert f.created_after is None
        assert f.created_before is None

    def test_enum_string_coercion(self):
        f = FactFilters(scope="team", memory_class="semantic")
        assert f.scope is Scope.TEAM
        assert f.memory_class is MemoryClass.SEMANTIC

    def test_confidence_bounds_enforced(self):
        assert FactFilters(min_confidence=0.0, max_confidence=1.0).max_confidence == 1.0
        with pytest.raises(ValidationError):
            FactFilters(min_confidence=-0.01)
        with pytest.raises(ValidationError):
            FactFilters(max_confidence=1.01)

    def test_round_trip(self):
        f = FactFilters(
            scope=Scope.ACTOR,
            memory_class=MemoryClass.POLICY,
            category="preference",
            archived=True,
            min_confidence=0.25,
            text_contains="python",
        )
        restored = FactFilters.model_validate(f.model_dump(mode="json"))
        assert restored == f


class TestFactPage:
    def test_defaults(self):
        p = FactPage()
        assert p.items == []
        assert p.total == 0
        assert p.page == 1
        assert p.page_size == 50
        assert p.total_pages == 0

    def test_carries_fact_items_round_trip(self):
        fact = FactAssertion(text="hello world")
        page = FactPage(items=[fact], total=1, page=2, page_size=10, total_pages=1)
        restored = FactPage.model_validate(page.model_dump(mode="json"))
        assert len(restored.items) == 1
        assert restored.items[0].text == "hello world"
        assert restored.page == 2


# ---------------------------------------------------------------------------
# dashboard.py — new transport DTOs
# ---------------------------------------------------------------------------


class TestMemoryBrowseRequest:
    def test_defaults(self):
        r = MemoryBrowseRequest()
        assert r.page == 1
        assert r.per_page == 50
        assert r.sort_by == "created_at"
        assert r.sort_order == "desc"
        assert r.scope is None
        assert r.memory_class is None
        assert r.category is None
        assert r.min_confidence is None
        assert r.source_actor_id is None
        assert r.goal_id is None

    def test_page_must_be_positive(self):
        with pytest.raises(ValidationError):
            MemoryBrowseRequest(page=0)

    def test_per_page_upper_bound(self):
        assert MemoryBrowseRequest(per_page=200).per_page == 200
        with pytest.raises(ValidationError):
            MemoryBrowseRequest(per_page=201)
        with pytest.raises(ValidationError):
            MemoryBrowseRequest(per_page=0)

    def test_min_confidence_bounds(self):
        assert MemoryBrowseRequest(min_confidence=0.5).min_confidence == 0.5
        with pytest.raises(ValidationError):
            MemoryBrowseRequest(min_confidence=1.5)
        with pytest.raises(ValidationError):
            MemoryBrowseRequest(min_confidence=-0.1)

    def test_uuid_fields_parsed(self):
        aid = uuid.uuid4()
        gid = uuid.uuid4()
        r = MemoryBrowseRequest(source_actor_id=str(aid), goal_id=str(gid))
        assert r.source_actor_id == aid
        assert r.goal_id == gid

    def test_enum_filters_coerced(self):
        r = MemoryBrowseRequest(scope="global", memory_class="episodic", category="identity")
        assert r.scope is Scope.GLOBAL
        assert r.memory_class is MemoryClass.EPISODIC


class TestGuardRuleUpdate:
    def test_all_fields_optional_none(self):
        u = GuardRuleUpdate()
        assert u.pattern is None
        assert u.pattern_type is None
        assert u.outcome is None
        assert u.description is None
        assert u.enabled is None
        assert u.min_approval_authority is None

    def test_extra_fields_forbidden(self):
        # model_config = ConfigDict(extra="forbid") — whitelisted fields only.
        with pytest.raises(ValidationError):
            GuardRuleUpdate(rule_id="deadbeef")

    def test_partial_update_round_trip(self):
        u = GuardRuleUpdate(enabled=False, min_approval_authority=3)
        dumped = u.model_dump()
        assert dumped["enabled"] is False
        assert dumped["min_approval_authority"] == 3
        assert dumped["pattern"] is None


class TestUserPreferences:
    def test_defaults(self):
        p = UserPreferences()
        assert p.actor_id is None
        assert p.default_page == "/"
        assert p.items_per_page == 50
        assert p.theme == "light"
        assert p.selected_gateway is None
        assert p.preferences == {}

    def test_items_per_page_bounds(self):
        assert UserPreferences(items_per_page=1).items_per_page == 1
        with pytest.raises(ValidationError):
            UserPreferences(items_per_page=201)
        with pytest.raises(ValidationError):
            UserPreferences(items_per_page=0)


class TestSavedView:
    def test_create_requires_nonempty_name_and_resource(self):
        with pytest.raises(ValidationError):
            SavedViewCreate(name="", resource="facts")
        with pytest.raises(ValidationError):
            SavedViewCreate(name="mine", resource="")

    def test_create_defaults(self):
        c = SavedViewCreate(name="High confidence", resource="facts")
        assert c.filters == {}
        assert c.sort == {}

    def test_saved_view_requires_id_name_resource(self):
        with pytest.raises(ValidationError):
            SavedView(name="x", resource="facts")  # missing id

    def test_saved_view_round_trip(self):
        v = SavedView(
            id="v1",
            name="recent",
            resource="facts",
            filters={"scope": "team"},
            sort={"field": "created_at"},
        )
        restored = SavedView.model_validate(v.model_dump(mode="json"))
        assert restored == v
        assert restored.created_at is None


class TestDashboardOverview:
    def test_defaults(self):
        o = DashboardOverview(time_range="24h")
        assert o.total_facts == 0
        assert o.facts_in_period == 0
        assert o.facts_by_class == {}
        assert o.facts_by_scope == {}
        assert o.active_sessions == 0
        assert o.total_actors == 0
        assert o.guard_triggers_in_period == 0
        assert o.guard_near_misses_in_period == 0
        assert o.errors_in_period == 0
        assert o.system_health == "healthy"
        assert o.components == {}
        assert o.recent_events == []

    def test_time_range_required(self):
        with pytest.raises(ValidationError):
            DashboardOverview()

    def test_components_round_trip(self):
        o = DashboardOverview(
            time_range="1h",
            components={"neo4j": ComponentHealth(status="ok", latency_ms=1.5)},
        )
        restored = DashboardOverview.model_validate(o.model_dump(mode="json"))
        assert restored.components["neo4j"].status == "ok"
        assert restored.components["neo4j"].latency_ms == 1.5


class TestComponentHealth:
    def test_latency_optional(self):
        h = ComponentHealth(status="not configured")
        assert h.latency_ms is None

    def test_status_required(self):
        with pytest.raises(ValidationError):
            ComponentHealth()


class TestGatewayInfo:
    def test_defaults(self):
        g = GatewayInfo(gateway_id="gw-1")
        assert g.org_id is None
        assert g.is_current is False

    def test_gateway_id_required(self):
        with pytest.raises(ValidationError):
            GatewayInfo()


class TestGraphNode:
    def test_required_and_defaults(self):
        n = GraphNode(id="eb-1", type="FactDataPoint")
        assert n.label == ""
        assert n.properties == {}

    def test_missing_id_or_type_rejected(self):
        with pytest.raises(ValidationError):
            GraphNode(type="FactDataPoint")
        with pytest.raises(ValidationError):
            GraphNode(id="eb-1")


class TestKnowledgeGraphResponse:
    def test_defaults(self):
        r = KnowledgeGraphResponse()
        assert r.nodes == []
        assert r.edges == []
        assert r.truncated is False
        assert r.node_count == 0
        assert r.edge_count == 0

    def test_round_trip_with_nodes(self):
        r = KnowledgeGraphResponse(
            nodes=[GraphNode(id="a", type="FactDataPoint", label="A")],
            node_count=1,
            truncated=True,
        )
        restored = KnowledgeGraphResponse.model_validate(r.model_dump(mode="json"))
        assert restored.nodes[0].id == "a"
        assert restored.truncated is True


class TestMemoryStatsResponse:
    def test_defaults(self):
        s = MemoryStatsResponse(time_range="7d")
        assert s.total_facts == 0
        assert s.by_class == {}
        assert s.by_scope == {}
        assert s.avg_confidence == 0.0
        assert s.avg_use_count == 0.0
        assert s.avg_success_rate == 0.0
        assert s.top_actors == []
        assert s.creation_over_time == []

    def test_top_actors_round_trip(self):
        s = MemoryStatsResponse(
            time_range="24h",
            top_actors=[ActorFactCount(actor_id="a1", actor_label="Alice", fact_count=7)],
        )
        restored = MemoryStatsResponse.model_validate(s.model_dump(mode="json"))
        assert restored.top_actors[0].actor_label == "Alice"
        assert restored.top_actors[0].fact_count == 7


class TestFactDetailResponse:
    def test_requires_fact_and_usage(self):
        with pytest.raises(ValidationError):
            FactDetailResponse()  # missing fact + usage

    def test_defaults_and_round_trip(self):
        fact = FactAssertion(text="detail me")
        resp = FactDetailResponse(fact=fact, usage=FactUsageSummary())
        assert resp.edges == []
        assert resp.claims == []
        assert resp.session_key is None
        assert resp.extraction_trace_event_id is None
        assert resp.usage.success_rate == 0.0

        restored = FactDetailResponse.model_validate(resp.model_dump(mode="json"))
        assert restored.fact.text == "detail me"
        assert restored.usage.use_count == 0

    def test_extraction_trace_event_id_uuid_parsed(self):
        ev_id = uuid.uuid4()
        resp = FactDetailResponse(
            fact=FactAssertion(text="x"),
            usage=FactUsageSummary(),
            extraction_trace_event_id=str(ev_id),
        )
        assert resp.extraction_trace_event_id == ev_id


# ---------------------------------------------------------------------------
# config.py — DashboardAuthConfig + AuditConfig fields + wiring (NEW)
# ---------------------------------------------------------------------------


class TestDashboardAuthConfig:
    def test_backward_compatible_defaults(self):
        c = DashboardAuthConfig()
        assert c.enabled is False  # no-enforcement by default
        assert c.core_uri == "http://localhost:3567"
        assert c.api_domain == "http://localhost:8420"
        assert c.website_domain == "http://localhost:5173"
        assert c.api_keys_db_path == "data/api_keys.db"
        assert c.preferences_db_path == "data/dashboard.db"
        assert c.bootstrap_complete is False
        assert c.static_dir == ""
        assert c.cookie_secure is False
        assert c.cookie_same_site == "lax"

    def test_strict_extra_forbidden(self):
        # Inherits _StrictBase (extra="forbid").
        with pytest.raises(ValidationError):
            DashboardAuthConfig(unknown_key="x")

    def test_round_trip(self):
        c = DashboardAuthConfig(enabled=True, static_dir="/srv/ui", cookie_secure=True)
        restored = DashboardAuthConfig.model_validate(c.model_dump())
        assert restored == c


class TestAuditConfigDashboardStores:
    def test_new_db_path_defaults(self):
        a = AuditConfig()
        assert a.api_keys_db_path == "data/api_keys.db"
        assert a.custom_guard_rules_db_path == "data/custom_guard_rules.db"
        assert a.dashboard_db_path == "data/dashboard.db"

    def test_paths_overridable(self):
        a = AuditConfig(dashboard_db_path="/tmp/dash.db")
        assert a.dashboard_db_path == "/tmp/dash.db"


class TestElephantBrokerConfigDashboardAuth:
    def test_dashboard_auth_default_factory(self):
        cfg = ElephantBrokerConfig()
        assert isinstance(cfg.dashboard_auth, DashboardAuthConfig)
        assert cfg.dashboard_auth.enabled is False

    def test_env_override_bindings_present(self):
        keys = {env for env, _, _ in ENV_OVERRIDE_BINDINGS}
        assert "EB_DASHBOARD_AUTH_ENABLED" in keys
        assert "EB_API_KEYS_DB_PATH" in keys
        assert "EB_DASHBOARD_DB_PATH" in keys
        # dashboard auth enable binding targets dashboard_auth.enabled as bool.
        binding = next(b for b in ENV_OVERRIDE_BINDINGS if b[0] == "EB_DASHBOARD_AUTH_ENABLED")
        assert binding[1] == "dashboard_auth.enabled"
        assert binding[2] == "bool"


# ---------------------------------------------------------------------------
# actor.py — ActorRef.active (NEW)
# ---------------------------------------------------------------------------


class TestActorRefActive:
    def test_active_defaults_true(self):
        a = ActorRef(type=ActorType.WORKER_AGENT, display_name="Worker")
        assert a.active is True

    def test_active_can_be_deactivated(self):
        a = ActorRef(type=ActorType.WORKER_AGENT, display_name="Worker", active=False)
        assert a.active is False

    def test_active_survives_round_trip(self):
        a = ActorRef(type=ActorType.SERVICE_ACTOR, display_name="svc", active=False)
        restored = ActorRef.model_validate(a.model_dump(mode="json"))
        assert restored.active is False


# ---------------------------------------------------------------------------
# trace.py — TraceEvent.fact_ids (NEW)
# ---------------------------------------------------------------------------


class TestTraceEventFactIds:
    def test_defaults_empty(self):
        ev = TraceEvent(event_type=TraceEventType.FACT_EXTRACTED)
        assert ev.fact_ids == []

    def test_accepts_uuid_list(self):
        ids = [uuid.uuid4(), uuid.uuid4()]
        ev = TraceEvent(event_type=TraceEventType.FACT_EXTRACTED, fact_ids=ids)
        assert ev.fact_ids == ids

    def test_round_trip_preserves_fact_ids(self):
        ids = [uuid.uuid4()]
        ev = TraceEvent(event_type=TraceEventType.FACT_EXTRACTED, fact_ids=ids)
        restored = TraceEvent.model_validate(ev.model_dump(mode="json"))
        assert restored.fact_ids == ids


# ---------------------------------------------------------------------------
# procedure.py — ProcedureDefinition.org_id / team_id (NEW)
# ---------------------------------------------------------------------------


class TestProcedureDefinitionScoping:
    def test_org_team_default_none(self):
        p = ProcedureDefinition(name="runbook", is_manual_only=True)
        assert p.org_id is None
        assert p.team_id is None

    def test_org_team_uuid_parsed(self):
        org = uuid.uuid4()
        team = uuid.uuid4()
        p = ProcedureDefinition(
            name="scoped",
            scope=Scope.TEAM,
            is_manual_only=True,
            org_id=str(org),
            team_id=str(team),
        )
        assert p.org_id == org
        assert p.team_id == team

    def test_scoping_round_trip(self):
        org = uuid.uuid4()
        p = ProcedureDefinition(name="p", is_manual_only=True, org_id=org)
        restored = ProcedureDefinition.model_validate(p.model_dump(mode="json"))
        assert restored.org_id == org
        assert restored.team_id is None

    def test_invalid_org_id_rejected(self):
        with pytest.raises(ValidationError):
            ProcedureDefinition(name="p", is_manual_only=True, org_id="not-a-uuid")
