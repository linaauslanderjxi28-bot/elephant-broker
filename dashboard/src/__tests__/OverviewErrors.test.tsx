/**
 * OverviewErrors.test.tsx — behaviour of the Home/Overview "Errors (range)"
 * tile drill-down and the self-explaining System-health chip
 * (`src/pages/home/index.tsx`).
 *
 * Unlike the dataProvider-backed pages, HomePage fetches through the
 * `dashboardApi` helpers (`apiGet` / `apiSend`) directly, so we mock that module
 * (keeping every real helper — relativeTime, humanizeEnum, colour maps — and
 * stubbing only the two network calls). Navigation flows through Refine's
 * router, so a stub `/trace` route lets us observe the pre-filtered deep link.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

// Mock only the two network helpers; keep the real formatters/label maps.
const apiGet = vi.fn();
const apiSend = vi.fn();
vi.mock("../pages/home/dashboardApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../pages/home/dashboardApi")>();
  return { ...actual, apiGet: (...a: any[]) => apiGet(...a), apiSend: (...a: any[]) => apiSend(...a) };
});

import HomePage from "../pages/home/index";

function fullOverview(overrides: Record<string, unknown> = {}) {
  return {
    time_range: "24h",
    total_facts: 10,
    facts_in_period: 3,
    facts_by_class: {},
    facts_by_scope: {},
    active_sessions: 1,
    total_actors: 2,
    total_organizations: 1,
    total_goals_active: 0,
    guard_triggers_in_period: 0,
    guard_near_misses_in_period: 0,
    errors_in_period: 0,
    system_health: "healthy",
    health_reasons: [],
    recent_errors: [],
    components: {},
    recent_events: [],
    ...overrides,
  };
}

// A stub trace page that surfaces the `types` query param so we can assert the
// "View all" deep link matches trace/list.tsx's URL filter shape.
function TraceStub() {
  const [params] = useSearchParams();
  return <div>trace-types:{params.get("types")}</div>;
}

function renderHome(overview: Record<string, unknown>) {
  apiGet.mockResolvedValue(overview);

  const authProvider = {
    login: vi.fn(async () => ({ success: true })),
    logout: vi.fn(async () => ({ success: true })),
    check: vi.fn(async () => ({ authenticated: true })),
    onError: vi.fn(async () => ({})),
    getPermissions: vi.fn(async () => ({ authorityLevel: 90 })),
    getIdentity: vi.fn(async () => ({ id: "act-1", authorityLevel: 90 })),
  } as any;

  const dataProvider = {
    getApiUrl: () => "http://test.local",
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    getOne: vi.fn(async () => ({ data: { id: "x" } })),
    custom: vi.fn(async () => ({ data: {} })),
  } as any;

  render(
    <MemoryRouter initialEntries={["/"]}>
      <Refine
        routerProvider={routerBindings}
        dataProvider={dataProvider}
        authProvider={authProvider}
        notificationProvider={{ open: vi.fn(), close: vi.fn() } as any}
        options={{ disableTelemetry: true, reactQuery: { devtoolConfig: false } }}
      >
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/trace" element={<TraceStub />} />
        </Routes>
      </Refine>
    </MemoryRouter>,
  );
}

describe("Overview — Errors drill-down + System-health explanation", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiSend.mockReset();
  });

  it("(a) Errors tile with count>0 is clickable and opens a dialog listing the recent errors", async () => {
    renderHome(
      fullOverview({
        errors_in_period: 2,
        system_health: "degraded",
        health_reasons: ["2 errors in the last 24h", "reranker: All connection attempts failed"],
        recent_errors: [
          {
            component: "reranker",
            error: "All connection attempts failed",
            timestamp: new Date().toISOString(),
            session_key: "agent:main:main",
          },
        ],
      }),
    );

    // Tile renders and is actionable (its label sits inside a CardActionArea
    // button); at count>0 the tile is a real button.
    const tileLabel = await screen.findByText("Errors (24h)");
    expect(tileLabel.closest("button")).not.toBeNull();

    // No dialog yet.
    expect(screen.queryByText("All connection attempts failed")).toBeNull();

    // Open the drill-down.
    fireEvent.click(tileLabel);

    // Dialog lists the mocked reranker error (component chip + raw message),
    // with the session key. No fallback fetch since recent_errors was populated.
    await screen.findByText("All connection attempts failed");
    expect(screen.getByText("reranker")).toBeTruthy();
    expect(screen.getByText(/agent:main:main/)).toBeTruthy();
    expect(apiSend).not.toHaveBeenCalled();

    // "View all" deep-links to the trace page pre-filtered to degraded_operation.
    fireEvent.click(screen.getByText("View all in Trace Explorer"));
    await screen.findByText("trace-types:degraded_operation");
  });

  it("(b) the Degraded chip explains itself via a health_reasons caption", async () => {
    renderHome(
      fullOverview({
        errors_in_period: 1,
        system_health: "degraded",
        health_reasons: ["1 error in the last 24h", "reranker: All connection attempts failed"],
        recent_errors: [
          {
            component: "reranker",
            error: "All connection attempts failed",
            timestamp: new Date().toISOString(),
            session_key: null,
          },
        ],
      }),
    );

    // Status chip.
    await screen.findByText("Degraded");
    // Each reason renders as a caption line beside/under the chip. The first
    // line carries the "Degraded — " status prefix (one Typography), so match
    // it by substring; the rest are the bare reason string.
    expect(screen.getByText(/Degraded — 1 error in the last 24h/)).toBeTruthy();
    expect(screen.getByText("reranker: All connection attempts failed")).toBeTruthy();
  });

  it("(c) count==0 tile is inert and no health reasons are shown when healthy", async () => {
    renderHome(fullOverview({ errors_in_period: 0, system_health: "healthy", health_reasons: [] }));

    const tileLabel = await screen.findByText("Errors (24h)");
    // Not wrapped in a CardActionArea button => inert.
    expect(tileLabel.closest("button")).toBeNull();

    // Clicking does nothing (no dialog).
    fireEvent.click(tileLabel);
    await waitFor(() => {
      expect(screen.queryByText(/Errors \(last/)).toBeNull();
    });

    // Healthy => no reason captions at all.
    expect(screen.queryByText(/in the last 24h/)).toBeNull();
    expect(screen.getByText("Healthy")).toBeTruthy();
  });
});
