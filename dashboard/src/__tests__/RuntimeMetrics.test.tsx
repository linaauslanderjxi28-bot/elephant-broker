/**
 * RuntimeMetrics.test.tsx — behavior of the "Runtime Metrics" section added to
 * the Memory Stats page (`src/pages/memory/stats.tsx`). The section fetches
 * `GET /dashboard/metrics` (the gateway-scoped Prometheus registry projection)
 * and renders cumulative counters/gauges as stat tiles plus an avg-latency row
 * derived from histogram _sum/_count.
 *
 * The data layer is mocked the same way sibling FE tests do it (a fake Refine
 * dataProvider whose `custom` handler routes by URL): the page issues one custom
 * GET to `/dashboard/memory/stats` (current-state totals) and one to
 * `/dashboard/metrics` (runtime metrics). We stub the former with a minimal
 * empty payload and drive assertions off the latter.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

import MemoryStats from "../pages/memory/stats";
import type { MetricsSnapshotResponse } from "../pages/memory/types";

// A minimal MemoryStatsResponse so the page's current-state section renders
// without crashing; all zeros keeps its numbers from colliding with the metric
// tile values (5 / 4 / 150.0 ms) we assert on below.
function emptyMemoryStats() {
  return {
    time_range: "24h",
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
  };
}

// Build a fake dataProvider whose `custom` routes by URL:
//  - `/dashboard/memory/stats` -> a minimal (empty) MemoryStatsResponse
//  - `/dashboard/metrics`       -> the supplied MetricsSnapshotResponse
function makeHarness(metrics: MetricsSnapshotResponse) {
  const custom = vi.fn(async (args: { url: string; method: string }) => {
    const { url } = args;
    if (url.includes("/dashboard/metrics")) {
      return { data: metrics };
    }
    if (url.includes("/dashboard/memory/stats")) {
      return { data: emptyMemoryStats() };
    }
    return { data: {} };
  });

  const dataProvider = {
    getApiUrl: () => "http://test.local",
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    getOne: vi.fn(async () => ({ data: { id: "x" } })),
    getMany: vi.fn(async () => ({ data: [] })),
    create: vi.fn(async () => ({ data: { id: "x" } })),
    update: vi.fn(async () => ({ data: { id: "x" } })),
    deleteOne: vi.fn(async () => ({ data: { id: "x" } })),
    custom,
  } as any;

  const authProvider = {
    login: vi.fn(async () => ({ success: true })),
    logout: vi.fn(async () => ({ success: true })),
    check: vi.fn(async () => ({ authenticated: true })),
    onError: vi.fn(async () => ({})),
    getPermissions: vi.fn(async () => ({ authorityLevel: 90 })),
    getIdentity: vi.fn(async () => ({ id: "act-1", authorityLevel: 90 })),
  } as any;

  const notificationProvider = { open: vi.fn(), close: vi.fn() } as any;

  render(
    <MemoryRouter initialEntries={["/memory/stats"]}>
      <Refine
        routerProvider={routerBindings}
        dataProvider={dataProvider}
        authProvider={authProvider}
        notificationProvider={notificationProvider}
        options={{ disableTelemetry: true, reactQuery: { devtoolConfig: false } }}
      >
        <Routes>
          <Route path="/memory/stats" element={<MemoryStats />} />
        </Routes>
      </Refine>
    </MemoryRouter>,
  );

  return { custom };
}

describe("Runtime Metrics section (GET /dashboard/metrics)", () => {
  it("renders counter, gauge, and histogram-avg tiles from the metrics payload", async () => {
    makeHarness({
      available: true,
      generated_at: "2026-07-03T15:13:07.123456+00:00",
      metrics: [
        {
          name: "eb_facts_stored_total",
          type: "counter",
          help: "Facts stored",
          // Two label sets summed -> total 5.
          series: [
            { labels: { memory_class: "semantic", profile_name: "coding" }, value: 2 },
            { labels: { memory_class: "episodic", profile_name: "coding" }, value: 3 },
          ],
        },
        {
          name: "eb_session_active",
          type: "gauge",
          help: "Active sessions",
          series: [{ labels: { profile_name: "coding" }, value: 4 }],
        },
        {
          name: "eb_retrieval_duration_seconds",
          type: "histogram",
          help: "Retrieval latency",
          // avg = 0.3 / 2 = 0.15s = 150.0 ms.
          series: [
            {
              labels: { auto_recall: "true", profile_name: "coding" },
              sum: 0.3,
              count: 2,
              buckets: { "0.25": 1, "+Inf": 2 },
            },
          ],
        },
      ],
    });

    // Section header + the "cumulative since process start" framing.
    await screen.findByText("Runtime Metrics");
    expect(
      screen.getByText(/Cumulative since process start/i),
    ).toBeTruthy();

    // Counter tile: Facts stored = 2 + 3 = 5.
    expect(screen.getByText("Facts stored")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();

    // Gauge tile: Active sessions = 4.
    expect(screen.getByText("Active sessions")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();

    // Histogram avg-latency tile: 150.0 ms.
    expect(screen.getByText("Retrieval")).toBeTruthy();
    expect(screen.getByText("150.0 ms")).toBeTruthy();
  });

  it("shows a subtle unavailable note when { available: false }", async () => {
    makeHarness({
      available: false,
      note: "prometheus_client is not installed; runtime metrics are disabled.",
    });

    // The degraded note renders instead of the tiles.
    expect(await screen.findByText(/Prometheus metrics unavailable/i)).toBeTruthy();

    // The section content (its header + any tiles) is NOT rendered.
    expect(screen.queryByText("Runtime Metrics")).toBeNull();
    expect(screen.queryByText("Facts stored")).toBeNull();
  });
});
