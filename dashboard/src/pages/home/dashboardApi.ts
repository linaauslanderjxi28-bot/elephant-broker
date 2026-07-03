// Shared helpers for ElephantBroker dashboard pages.
//
// This module centralizes the small amount of cross-page glue the page
// components need: a credentialed fetch wrapper that always injects the
// selected gateway_id (matching the data provider's convention), the static
// label/colour maps the plan defines, an authority hook, and a relative-time
// formatter. It intentionally has NO dependency on the shared components owned
// by other agents so the page bundle stays self-contained and functional.

import { usePermissions } from "@refinedev/core";

import { getSelectedGateway } from "../../providers/apiClient";
import { SELECTED_GATEWAY_KEY } from "../../providers/gatewayKey";
import { humanizeEnum } from "../../lib/format";

export const API_URL: string =
  ((import.meta as any).env?.VITE_EB_RUNTIME_URL as string | undefined) ||
  "http://localhost:8420";

/**
 * Canonical localStorage key for the active gateway (re-exported from the
 * shared gatewayKey module).
 * @deprecated import `SELECTED_GATEWAY_KEY` from providers/gatewayKey, or use
 * the get/set helpers there / in providers/apiClient instead of touching
 * localStorage directly.
 */
export const GATEWAY_STORAGE_KEY = SELECTED_GATEWAY_KEY;

/**
 * Active gateway_id ("" => runtime default). Delegates to the apiClient
 * module-level store, which GatewaySelector updates and which is hydrated
 * from (and persisted to) the canonical localStorage key.
 */
export function getGatewayId(): string {
  return getSelectedGateway();
}

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const base = path.startsWith("http") ? path : `${API_URL}${path}`;
  const url = new URL(base, window.location.origin);
  const gw = getGatewayId();
  if (gw) url.searchParams.set("gateway_id", gw);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }
  return url.toString();
}

/**
 * Error thrown by {@link apiGet} / {@link apiSend} for a non-2xx response.
 *
 * Historically these helpers threw `new Error("404 Not Found")`, discarding the
 * response body — so call sites could never surface FastAPI's `{detail}` (string
 * or 422 validation array) and forms lost field-level errors (RC-4:
 * guards-profiles-9, goals-procedures-10, gap-5-2). This carries the parsed body
 * on `.body` so any caller can run it through `normalizeApiError(err)` (which
 * reads `.body`) to get a human message plus per-field errors.
 *
 * The `.message` is intentionally kept as `"{status} {statusText}"` for backward
 * compatibility with the few call sites that branch on the status prefix
 * (e.g. `err.message.startsWith("501")`); prefer `.status` / `normalizeApiError`
 * in new code.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Alias of {@link status}; matches the `statusCode` convention used elsewhere. */
  readonly statusCode: number;
  readonly statusText: string;
  /** Parsed JSON response body (or the raw text, or null). */
  readonly body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    super(`${status} ${statusText}`.trim());
    this.name = "ApiError";
    this.status = status;
    this.statusCode = status;
    this.statusText = statusText;
    this.body = body;
  }
}

/** Read a failed Response into an ApiError, preserving the parsed body. */
async function toApiError(res: Response): Promise<ApiError> {
  let raw = "";
  try {
    raw = await res.text();
  } catch {
    /* body unavailable — keep the status line only */
  }
  let body: unknown = raw || null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = raw;
    }
  }
  return new ApiError(res.status, res.statusText, body);
}

export async function apiGet<T = any>(
  path: string,
  params?: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(buildUrl(path, params), { credentials: "include" });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export async function apiSend<T = any>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  params?: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(buildUrl(path, params), {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await toApiError(res);
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

// --- Authority ---------------------------------------------------------------

export function useAuthority(): number {
  const { data } = usePermissions<{ authorityLevel?: number }>();
  return (data?.authorityLevel as number | undefined) ?? 0;
}

export function authorityLabel(level: number): string {
  if (level >= 90) return "System Admin";
  if (level >= 70) return "Org Admin";
  if (level >= 50) return "Team Lead";
  return "Regular";
}

export const AUTHORITY_OPTIONS: Array<{ label: string; value: number }> = [
  { label: "Regular (0)", value: 0 },
  { label: "Team Lead (50)", value: 50 },
  { label: "Org Admin (70)", value: 70 },
  { label: "System Admin (90)", value: 90 },
];

// --- Static label / colour maps (from the plan) ------------------------------

export const COMPONENT_LABELS: Record<string, string> = {
  neo4j: "Graph Store",
  qdrant: "Vector Store",
  redis: "Cache",
  llm: "Language Model",
  embedding: "Embeddings",
  clickhouse: "Analytics Store",
};

export const SOURCE_LABELS: Record<string, string> = {
  cognee_graph: "Graph",
  cognee_chunks: "Semantic",
  cypher: "Structural",
  hybrid: "Hybrid",
  artifacts: "Artifact",
  chunks_lexical: "Keyword",
};

type MuiColor =
  | "default"
  | "primary"
  | "secondary"
  | "error"
  | "info"
  | "success"
  | "warning";

export function memoryClassColor(cls: string | undefined): MuiColor {
  switch ((cls || "").toUpperCase()) {
    case "EPISODIC":
      return "info";
    case "SEMANTIC":
      return "success";
    case "PROCEDURAL":
      return "secondary";
    case "POLICY":
      return "error";
    case "WORKING":
    case "WORKING_MEMORY":
      return "default";
    default:
      return "default";
  }
}

export function goalStatusColor(status: string | undefined): MuiColor {
  switch ((status || "").toLowerCase()) {
    case "active":
      return "success";
    case "paused":
      return "warning";
    case "completed":
      return "info";
    case "abandoned":
      return "default";
    default:
      return "default";
  }
}

export function guardOutcomeColor(outcome: string | undefined): MuiColor {
  switch ((outcome || "").toLowerCase()) {
    case "block":
    case "blocked":
      return "error";
    case "require_approval":
      return "warning";
    case "near_miss":
    case "warn":
      return "warning";
    case "pass":
    case "allow":
    case "log_only":
      return "success";
    default:
      return "default";
  }
}

export const SCOPE_OPTIONS = [
  "GLOBAL",
  "ORGANIZATION",
  "TEAM",
  "ACTOR",
  "SESSION",
] as const;

export const MEMORY_CLASS_OPTIONS = [
  "EPISODIC",
  "SEMANTIC",
  "PROCEDURAL",
  "POLICY",
  "WORKING",
] as const;

export const DECISION_DOMAINS = [
  "CODE_CHANGE",
  "FINANCIAL",
  "DATA_ACCESS",
  "COMMUNICATION",
  "INFRASTRUCTURE",
  "EXTERNAL_COMM",
  "COMPLIANCE",
  "PERSONNEL",
  "SECURITY",
  "DEPLOYMENT",
] as const;

export const ACTOR_TYPE_GROUPS: Record<string, string[]> = {
  Humans: ["HUMAN_COORDINATOR", "HUMAN_OPERATOR", "EXTERNAL_HUMAN"],
  Agents: [
    "MANAGER_AGENT",
    "WORKER_AGENT",
    "REVIEWER_AGENT",
    "SUPERVISOR_AGENT",
    "PEER_AGENT",
    "EXTERNAL_AGENT",
  ],
  Service: ["SERVICE_ACTOR", "ORGANIZATION_ACTOR", "TEAM_ACTOR"],
};

export function actorTypeColor(type: string | undefined): MuiColor {
  const t = (type || "").toUpperCase();
  if (ACTOR_TYPE_GROUPS.Humans.includes(t)) return "primary";
  if (ACTOR_TYPE_GROUPS.Agents.includes(t)) return "success";
  return "default";
}

// Scopes selectable at a given authority level (goals / procedures).
export function scopesForAuthority(level: number): string[] {
  if (level >= 90) return ["GLOBAL", "ORGANIZATION", "TEAM", "ACTOR"];
  if (level >= 70) return ["ORGANIZATION", "TEAM", "ACTOR"];
  if (level >= 50) return ["TEAM", "ACTOR"];
  return ["ACTOR"];
}

// --- Trace event summaries (mirror of the server-side map) -------------------

/**
 * Distinguish the several `session_boundary` sub-events by their `payload.event`
 * discriminator (`start` | `end` | `lifecycle_session_end` | `engine_teardown`
 * | `goals_flushed`). Previously every boundary rendered as "Session ended",
 * mislabelling session STARTS (sessions-5, consolidation-trace-4).
 */
function summarizeSessionBoundary(p: Record<string, any>): string {
  switch (String(p.event ?? "").toLowerCase()) {
    case "start":
      return "Session started";
    case "end":
    case "lifecycle_session_end":
      return "Session ended";
    case "engine_teardown":
      return "Session engine torn down";
    case "goals_flushed":
      return "Session goals flushed";
    default:
      return "Session boundary";
  }
}

export function summarizeEvent(
  eventType: string,
  payload: Record<string, any> = {},
): string {
  const p = payload || {};
  switch (eventType) {
    case "fact_extracted": {
      const text = String(p.text ?? "").trim();
      if (text) return `New fact extracted: ${text.slice(0, 60)}`;
      return typeof p.facts_count === "number"
        ? `Facts extracted: ${p.facts_count}`
        : "Facts extracted";
    }
    case "retrieval_performed":
      // The trace ledger emits `results` (retrieval count); `result_count` was
      // never written, so this always printed "? results" (overview-1,
      // consolidation-trace-3). Fall back through the legacy/candidate keys.
      return `Memory search: ${p.results ?? p.result_count ?? p.candidate_count ?? "?"} results`;
    case "context_assembled":
      return `Context assembled: ${p.total_tokens ?? "?"} tokens`;
    case "guard_triggered": {
      const domain = humanizeEnum(p.decision_domain);
      const what = p.action_target || humanizeEnum(p.outcome) || "action";
      return domain
        ? `Guard triggered: ${what} (${domain})`
        : `Guard triggered: ${what}`;
    }
    case "guard_near_miss": {
      const domain = humanizeEnum(p.decision_domain);
      return domain ? `Guard near-miss (${domain})` : "Guard near-miss";
    }
    case "scoring_completed":
      return `Scoring: ${p.candidate_count ?? "?"} candidates ranked`;
    case "compaction_action":
      return `Compaction: ${humanizeEnum(p.trigger) || "action"}`;
    case "degraded_operation":
      return `Error: ${p.error ?? "unknown"}`;
    case "session_boundary":
      return summarizeSessionBoundary(p);
    case "bootstrap_completed":
      return `Session started: profile ${p.profile_name ?? "?"}`;
    default:
      // Unknown/uncatalogued event types: show a humanized label rather than
      // the raw snake_case token (overview-2, consolidation-trace-5).
      return humanizeEnum(eventType) || eventType;
  }
}

export function eventChipColor(eventType: string): MuiColor {
  switch (eventType) {
    case "guard_triggered":
    case "degraded_operation":
      return "error";
    case "guard_near_miss":
    case "compaction_action":
      return "warning";
    case "fact_extracted":
      return "success";
    case "retrieval_performed":
    case "context_assembled":
    case "scoring_completed":
      return "info";
    default:
      return "default";
  }
}

// --- Time formatting ---------------------------------------------------------

export function relativeTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const diff = Date.now() - then;
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? "ago" : "from now";
  const mins = Math.round(abs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ${suffix}`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hr ${suffix}`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ${suffix}`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months} mo ${suffix}`;
  const years = Math.round(months / 12);
  return `${years} yr${years === 1 ? "" : "s"} ${suffix}`;
}

export const TIME_RANGES = ["1h", "6h", "24h", "7d"] as const;
export type TimeRange = (typeof TIME_RANGES)[number];

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
