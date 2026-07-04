// Shared types and display maps for the Memory section (Phase 11 dashboard).
//
// These mirror the Python Pydantic schemas in `elephantbroker/schemas/fact.py`
// and the dashboard response schemas defined in the Phase 11 plan (Section 2).
// They are kept local to the memory pages so the section stays self-contained;
// the runtime dataProvider (fe:providers) transports these shapes over
// `/dashboard/memory/*` and `/memory/search`.

import { humanizeEnum } from "../../lib/format";

// ---------------------------------------------------------------------------
// Enums (string unions matching the backend StrEnum values)
// ---------------------------------------------------------------------------

export type MemoryClass =
  | "episodic"
  | "semantic"
  | "procedural"
  | "policy"
  | "working_memory";

export type Scope =
  | "global"
  | "organization"
  | "team"
  | "actor"
  | "session"
  | "task"
  | "subagent"
  | "artifact";

export type FactCategory =
  | "identity"
  | "preference"
  | "event"
  | "decision"
  | "system"
  | "relationship"
  | "trait"
  | "project"
  | "general"
  | "constraint"
  | "procedure_ref"
  | "verification";

// ---------------------------------------------------------------------------
// Core fact shape (subset of FactAssertion used by the UI)
// ---------------------------------------------------------------------------

export interface FactAssertion {
  id: string;
  text: string;
  category: string;
  scope: Scope;
  confidence: number;
  memory_class: MemoryClass;
  session_key?: string | null;
  session_id?: string | null;
  source_actor_id?: string | null;
  target_actor_ids?: string[];
  goal_ids?: string[];
  created_at: string;
  updated_at: string;
  last_used_at?: string | null;
  use_count: number;
  successful_use_count: number;
  freshness_score?: number | null;
  provenance_refs?: string[];
  embedding_ref?: string | null;
  token_size?: number | null;
  goal_relevance_tags?: Record<string, string>;
  gateway_id?: string;
  decision_domain?: string | null;
  archived?: boolean;
  autorecall_blacklisted?: boolean;
  // Optional cognee linkage surfaced by the detail endpoint when present.
  cognee_data_id?: string | null;
  eb_id?: string | null;
}

export interface PaginatedResult<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

// ---------------------------------------------------------------------------
// Fact detail (GET /dashboard/memory/{fact_id}/detail)
// ---------------------------------------------------------------------------

export interface FactEdge {
  relation_type: string;
  direction: "outgoing" | "incoming";
  target_id: string;
  target_type: string;
  target_label: string;
  target_properties: Record<string, unknown>;
}

export interface LinkedClaim {
  claim_id: string;
  claim_text: string;
  status: string;
  evidence_count: number;
}

// A single evidence receipt attached to a claim, as returned by the runtime
// `GET /claims/{claim_id}` endpoint (elephantbroker/schemas/evidence.py
// EvidenceRef). Surfaced in the Fact Detail claims panel so reviewers can
// inspect the receipts *before* deciding to verify/reject.
export interface EvidenceRef {
  id: string;
  type: string;
  ref_value: string;
  content_hash?: string | null;
  created_at?: string | null;
  created_by_actor_id?: string | null;
}

// `GET /claims/{claim_id}` — full claim record (evidence + review verdict).
// `rejection_reason` is the durable field written by EvidenceEngine.reject();
// reviewers must be able to read *why* a claim was rejected.
export interface ClaimDetailResponse {
  claim_id: string;
  status: string;
  evidence_refs: EvidenceRef[];
  verifier_actor_id?: string | null;
  verified_at?: string | null;
  rejection_reason?: string | null;
}

export interface FactUsageSummary {
  use_count: number;
  successful_use_count: number;
  success_rate: number;
  last_used_at?: string | null;
  superseded_by?: string | null;
  goal_relevance_tags?: Record<string, string>;
}

export interface FactDetailResponse {
  fact: FactAssertion;
  edges: FactEdge[];
  claims: LinkedClaim[];
  usage: FactUsageSummary;
  session_key?: string | null;
  extraction_trace_event_id?: string | null;
}

// ---------------------------------------------------------------------------
// Search (POST /memory/search)
// ---------------------------------------------------------------------------

export interface SearchResult extends FactAssertion {
  score: number;
  source: string;
}

export interface SearchRequest {
  query: string;
  max_results?: number;
  min_score?: number;
  scope?: Scope | null;
  actor_id?: string | null;
  memory_class?: MemoryClass | null;
  session_key?: string | null;
  profile_name?: string | null;
  auto_recall?: boolean;
}

// ---------------------------------------------------------------------------
// Stats (GET /dashboard/memory/stats)
// ---------------------------------------------------------------------------

export interface ActorFactCount {
  actor_id: string;
  actor_label: string;
  fact_count: number;
}

export interface TimeBucket {
  timestamp: string;
  count: number;
}

export interface MemoryStatsResponse {
  time_range: string;
  total_facts: number;
  by_class: Record<string, number>;
  by_scope: Record<string, number>;
  avg_confidence: number;
  avg_use_count: number;
  avg_success_rate: number;
  top_actors: ActorFactCount[];
  extractions_in_period: number;
  dedup_rate: number;
  supersession_rate: number;
  creation_over_time: TimeBucket[];
  /**
   * Which store actually served the activity rates + sparkline at request time:
   * "clickhouse" = the durable OTEL trace store (full selected range),
   * "ledger" = the bounded in-memory trace ledger (window may be capped).
   * Backend emits these additively (see api/routes/dashboard.py memory_stats).
   */
  activity_source?: "clickhouse" | "ledger";
  /** Human-readable label for `activity_source`, e.g. "ClickHouse (durable)". */
  activity_source_label?: string;
  /** True when the ledger path could not cover the full selected range. */
  activity_window_capped?: boolean;
  /** Ledger buffer retention in seconds when `activity_window_capped`, else null. */
  activity_retention_seconds?: number | null;
  /** Optional truthful note about how activity data was served. */
  note?: string;
}

// ---------------------------------------------------------------------------
// Runtime metrics (GET /dashboard/metrics)
// ---------------------------------------------------------------------------
// Gateway-scoped JSON projection of the in-process Prometheus registry, mirroring
// the Pydantic schemas in elephantbroker/schemas/dashboard.py (MetricSeries /
// MetricSnapshot / MetricsSnapshotResponse). The backend strips `gateway_id`
// from every series label and emits fields with `exclude_none`, so counter/gauge
// series carry only {labels, value} while histogram series carry only
// {labels, sum, count, buckets}. A metric with no series for the caller's gateway
// is omitted from the array entirely. Values are cumulative since process start
// (they reset on restart) — NOT the Neo4j current-state totals shown elsewhere.

export type MetricType = "counter" | "gauge" | "histogram";

/** One labelled series within a metric family (gateway_id already stripped). */
export interface MetricSeries {
  /** Secondary labels only (e.g. memory_class, profile_name); gateway_id removed. */
  labels: Record<string, string>;
  /** Counters / gauges — the current sample value. */
  value?: number;
  /** Histograms — observation sum (paired with `count` for avg = sum/count). */
  sum?: number;
  /** Histograms — observation count. */
  count?: number;
  /** Histograms — cumulative counts keyed by upper bound (`le`), e.g. "+Inf". */
  buckets?: Record<string, number>;
}

/** One Prometheus metric family, aggregated to the caller's gateway. */
export interface MetricSnapshot {
  /** Exposed series name, e.g. "eb_facts_stored_total". */
  name: string;
  type: MetricType;
  help?: string;
  series: MetricSeries[];
}

/**
 * `GET /dashboard/metrics` response. When `prometheus_client` is absent the
 * backend degrades to `{ available: false, note }` (HTTP 200, no `generated_at`
 * and no `metrics`), so the FE treats missing/empty `metrics` as "none".
 */
export interface MetricsSnapshotResponse {
  available: boolean;
  /** ISO-8601 UTC snapshot time; absent on the degraded response. */
  generated_at?: string;
  /** Truthful note (e.g. why metrics are disabled). */
  note?: string;
  metrics?: MetricSnapshot[];
}

// ---------------------------------------------------------------------------
// Knowledge graph (GET /dashboard/memory/graph)
// ---------------------------------------------------------------------------

/** A single knowledge-graph node projected from a gateway-scoped Cypher row. */
export interface MemoryGraphNode {
  /** eb_id */
  id: string;
  /** labels(n)[0], e.g. "FactDataPoint" */
  type: string;
  /** coalesce(display_name, title, name, left(text,80), eb_id) */
  label: string;
  /**
   * Curated scalar projection (None keys dropped by the backend):
   * scope, memory_class, category, confidence, status, actor_type,
   * authority_level, source_actor_id, archived, created_at_ms (int epoch-ms).
   */
  properties: Record<string, unknown>;
}

/** A directed, typed edge between two in-gateway nodes. */
export interface MemoryGraphEdge {
  source: string;
  target: string;
  /**
   * type(r): ABOUT_ACTOR | CREATED_BY | SERVES_GOAL | CHILD_OF | SUPPORTS |
   * MEMBER_OF | OWNED_BY | BELONGS_TO | SUPERSEDES
   */
  relation_type: string;
}

/** Gateway-scoped subgraph for the Obsidian-style memory graph explorer. */
export interface MemoryGraphResponse {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  /** len(nodes) >= max_nodes */
  truncated: boolean;
  node_count: number;
  edge_count: number;
}

// ---------------------------------------------------------------------------
// Display maps
// ---------------------------------------------------------------------------

export type ChipColor =
  | "default"
  | "primary"
  | "secondary"
  | "error"
  | "info"
  | "success"
  | "warning";

export const MEMORY_CLASS_LABELS: Record<MemoryClass, string> = {
  episodic: "Episodic",
  semantic: "Semantic",
  procedural: "Procedural",
  policy: "Policy",
  working_memory: "Working Memory",
};

// Color-coding aligned with the plan's browse DataGrid chip legend:
// Episodic (blue), Semantic (green), Procedural (purple), Policy (red),
// Working Memory (gray). MUI palette has no purple, so procedural uses a
// hex override applied by the chip renderer.
export const MEMORY_CLASS_COLORS: Record<MemoryClass, ChipColor> = {
  episodic: "info",
  semantic: "success",
  procedural: "secondary",
  policy: "error",
  working_memory: "default",
};

export const MEMORY_CLASS_HEX: Partial<Record<MemoryClass, string>> = {
  procedural: "#7b3fe4",
};

export const SCOPE_LABELS: Record<Scope, string> = {
  global: "Global",
  organization: "Organization",
  team: "Team",
  actor: "Agent",
  session: "Session",
  task: "Task",
  subagent: "Subagent",
  artifact: "Artifact",
};

export const CATEGORY_LABELS: Record<string, string> = {
  identity: "Identity",
  preference: "Preference",
  event: "Event",
  decision: "Decision",
  system: "System",
  relationship: "Relationship",
  trait: "Trait",
  project: "Project",
  general: "General",
  constraint: "Constraint",
  procedure_ref: "Procedure Ref",
  verification: "Verification",
};

// Retrieval `source` field -> user-friendly label (plan Section 2 Search).
//
// Keys MUST match the exact `source` strings the backend retrieval orchestrator
// emits (elephantbroker/runtime/retrieval/orchestrator.py): "structural",
// "keyword", "vector", "graph", "artifact", plus the facade fallback "hybrid".
// Any unrecognized source falls back to humanizeEnum() via sourceLabel().
export const SOURCE_LABELS: Record<string, string> = {
  structural: "Structural",
  keyword: "Keyword",
  vector: "Semantic",
  graph: "Graph",
  artifact: "Artifact",
  hybrid: "Hybrid",
};

export const SOURCE_TOOLTIPS: Record<string, string> = {
  Graph: "Found via Cognee graph completion over the knowledge graph.",
  Semantic: "Found via semantic chunk similarity (vector search).",
  Structural: "Found via a structural Cypher property match.",
  Hybrid: "Found by the merged hybrid retriever.",
  Artifact: "Found in ingested artifacts.",
  Keyword: "Found via lexical keyword matching.",
};

export const MEMORY_CLASS_OPTIONS: MemoryClass[] = [
  "episodic",
  "semantic",
  "procedural",
  "policy",
  "working_memory",
];

export const SCOPE_OPTIONS: Scope[] = [
  "global",
  "organization",
  "team",
  "actor",
  "session",
  "task",
  "subagent",
  "artifact",
];

export const CATEGORY_OPTIONS: string[] = [
  "identity",
  "preference",
  "event",
  "decision",
  "system",
  "relationship",
  "trait",
  "project",
  "general",
  "constraint",
  "procedure_ref",
  "verification",
];

export const PROFILE_OPTIONS: string[] = [
  "coding",
  "research",
  "managerial",
  "worker",
  "personal_assistant",
];

// Authority thresholds (SOW 11.3 access control).
export const AUTH_EDIT = 50; // edit / promote
export const AUTH_DELETE = 70; // GDPR delete

export function factClassLabel(cls: string): string {
  return MEMORY_CLASS_LABELS[cls as MemoryClass] ?? cls;
}

export function scopeLabel(scope: string): string {
  return SCOPE_LABELS[scope as Scope] ?? scope;
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? humanizeEnum(source);
}
