/**
 * memoryTypes.test.ts — unit tests for the pure display helpers and the
 * branch-new response shapes in `src/pages/memory/types.ts` (Phase 11 dashboard,
 * branch EB-FE).
 *
 * The module is pure — it performs no I/O (no network, storage, or SDK calls) —
 * so, like labels.test.ts, there is nothing to mock. These tests cover the NEW
 * behavior on this branch that MemoryBrowser.test.tsx does NOT already exercise:
 *
 *   - factClassLabel: exact human labels for every known MemoryClass
 *     (MemoryBrowser only checks the unknown-value fallback).
 *   - scopeLabel: exact human labels for every known Scope — notably the
 *     non-obvious `actor` -> "Agent" remap — plus the unknown fallback
 *     (MemoryBrowser only asserts scopeLabel("team") is a non-empty string).
 *   - sourceLabel: the rewritten SOURCE_LABELS key set (structural/keyword/
 *     vector/graph/artifact/hybrid) and the new humanizeEnum() fallback for
 *     unrecognized sources (uncommitted diff on this branch).
 *   - MemoryStatsResponse: presence/round-trip of the additive optional
 *     activity_source* / note fields the backend now emits.
 */

import { describe, it, expect } from "vitest";

import {
  factClassLabel,
  scopeLabel,
  sourceLabel,
  MEMORY_CLASS_OPTIONS,
  SCOPE_OPTIONS,
  type MemoryClass,
  type Scope,
  type MemoryStatsResponse,
} from "../pages/memory/types";

describe("factClassLabel — known MemoryClass values map to human labels", () => {
  const cases: Array<[MemoryClass, string]> = [
    ["episodic", "Episodic"],
    ["semantic", "Semantic"],
    ["procedural", "Procedural"],
    ["policy", "Policy"],
    ["working_memory", "Working Memory"],
  ];

  it.each(cases)("labels %s as %s", (cls, label) => {
    expect(factClassLabel(cls)).toBe(label);
  });

  it("has a label for every declared MemoryClass option", () => {
    for (const cls of MEMORY_CLASS_OPTIONS) {
      const label = factClassLabel(cls);
      expect(label.length).toBeGreaterThan(0);
      // A mapped label is never the raw snake_case token echoed back.
      expect(label).not.toBe(cls);
    }
  });

  it("falls back to the raw value for an unknown class", () => {
    expect(factClassLabel("mystery_class")).toBe("mystery_class");
    expect(factClassLabel("")).toBe("");
  });
});

describe("scopeLabel — known Scope values map to human labels", () => {
  const cases: Array<[Scope, string]> = [
    ["global", "Global"],
    ["organization", "Organization"],
    ["team", "Team"],
    // `actor` is deliberately surfaced to end-users as "Agent".
    ["actor", "Agent"],
    ["session", "Session"],
    ["task", "Task"],
    ["subagent", "Subagent"],
    ["artifact", "Artifact"],
  ];

  it.each(cases)("labels %s as %s", (scope, label) => {
    expect(scopeLabel(scope)).toBe(label);
  });

  it("remaps `actor` away from the raw token (not a passthrough)", () => {
    expect(scopeLabel("actor")).toBe("Agent");
    expect(scopeLabel("actor")).not.toBe("actor");
  });

  it("has a label for every declared Scope option", () => {
    for (const scope of SCOPE_OPTIONS) {
      const label = scopeLabel(scope);
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it("falls back to the raw value for an unknown scope", () => {
    expect(scopeLabel("nowhere")).toBe("nowhere");
    expect(scopeLabel("")).toBe("");
  });
});

describe("sourceLabel — rewritten SOURCE_LABELS key set (branch-new)", () => {
  const cases: Array<[string, string]> = [
    ["structural", "Structural"],
    ["keyword", "Keyword"],
    ["vector", "Semantic"],
    ["graph", "Graph"],
    ["artifact", "Artifact"],
    ["hybrid", "Hybrid"],
  ];

  it.each(cases)("labels retrieval source %s as %s", (source, label) => {
    expect(sourceLabel(source)).toBe(label);
  });

  it("no longer recognizes the pre-branch source keys, humanizing them instead", () => {
    // These were the old SOURCE_LABELS keys before the uncommitted rewrite;
    // they now miss the map and route through humanizeEnum().
    expect(sourceLabel("cognee_graph")).toBe("Cognee Graph");
    expect(sourceLabel("cognee_chunks")).toBe("Cognee Chunks");
    expect(sourceLabel("cypher")).toBe("Cypher");
    expect(sourceLabel("chunks_lexical")).toBe("Chunks Lexical");
  });

  it("falls back to humanizeEnum() for an unrecognized source", () => {
    // Unknown snake_case / kebab-case / SCREAMING_CASE tokens become Title Case.
    expect(sourceLabel("some_new_source")).toBe("Some New Source");
    expect(sourceLabel("brand-new")).toBe("Brand New");
    expect(sourceLabel("GRAPH_COMPLETION")).toBe("Graph Completion");
  });

  it("maps an empty source to an empty string (humanizeEnum('') === '')", () => {
    expect(sourceLabel("")).toBe("");
  });
});

describe("MemoryStatsResponse — additive optional activity_source fields", () => {
  it("accepts and round-trips the full set of optional activity fields", () => {
    const stats: MemoryStatsResponse = {
      time_range: "7d",
      total_facts: 3,
      by_class: { semantic: 2, episodic: 1 },
      by_scope: { team: 3 },
      avg_confidence: 0.8,
      avg_use_count: 1.2,
      avg_success_rate: 0.5,
      top_actors: [],
      extractions_in_period: 4,
      dedup_rate: 0.1,
      supersession_rate: 0.05,
      creation_over_time: [{ timestamp: "2026-07-02T00:00:00Z", count: 3 }],
      // Branch-new additive fields:
      activity_source: "clickhouse",
      activity_source_label: "ClickHouse (durable)",
      activity_window_capped: false,
      activity_retention_seconds: null,
      note: "Served from the durable OTEL trace store.",
    };

    expect(stats).toHaveProperty("activity_source", "clickhouse");
    expect(stats).toHaveProperty("activity_source_label", "ClickHouse (durable)");
    expect(stats).toHaveProperty("activity_window_capped", false);
    expect(stats).toHaveProperty("activity_retention_seconds", null);
    expect(stats).toHaveProperty("note");
  });

  it("supports the `ledger` variant with a capped, bounded window", () => {
    const stats: MemoryStatsResponse = {
      time_range: "30d",
      total_facts: 0,
      by_class: {},
      by_scope: {},
      avg_confidence: 0,
      avg_use_count: 0,
      avg_success_rate: 0,
      top_actors: [],
      extractions_in_period: 0,
      dedup_rate: 0,
      supersession_rate: 0,
      creation_over_time: [],
      activity_source: "ledger",
      activity_source_label: "In-memory ledger (bounded)",
      activity_window_capped: true,
      activity_retention_seconds: 3600,
    };

    expect(stats.activity_source).toBe("ledger");
    expect(stats.activity_window_capped).toBe(true);
    expect(stats.activity_retention_seconds).toBe(3600);
    // `note` is optional and legitimately absent here.
    expect(stats.note).toBeUndefined();
  });

  it("remains valid with none of the optional activity fields present", () => {
    const stats: MemoryStatsResponse = {
      time_range: "24h",
      total_facts: 1,
      by_class: { policy: 1 },
      by_scope: { global: 1 },
      avg_confidence: 1,
      avg_use_count: 0,
      avg_success_rate: 0,
      top_actors: [],
      extractions_in_period: 1,
      dedup_rate: 0,
      supersession_rate: 0,
      creation_over_time: [],
    };

    // The base (pre-branch) shape carries none of the new optional keys.
    expect(stats.activity_source).toBeUndefined();
    expect(stats.activity_source_label).toBeUndefined();
    expect(stats.activity_window_capped).toBeUndefined();
    expect(stats.activity_retention_seconds).toBeUndefined();
    expect(stats.note).toBeUndefined();
    expect(Object.keys(stats)).not.toContain("activity_source");
  });
});
