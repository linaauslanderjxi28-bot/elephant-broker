/**
 * FactIndexesPage.test.tsx — page-level tests for the Fact Indexes settings
 * page (`src/pages/settings/indexes.tsx`). Renders inside the same minimal
 * Refine + router harness as ActorsListPage.test.tsx (mocked auth/notification
 * providers, stubbed global `fetch`) and pins the Fix 5 FE contract:
 *
 *  - render states: Off (absent) / Online / Populating N% / Failed chips,
 *    straight from GET /admin/indexes (Neo4j is the source of truth);
 *  - the enable toggle POSTs /admin/indexes/{name}, the disable toggle
 *    DELETEs it — never anything implicit at render time;
 *  - rebuild requires the confirm dialog, then POSTs .../{name}/rebuild;
 *  - errors (e.g. 503 context-only) surface as an alert;
 *  - while an index is POPULATING the page re-polls the status endpoint and
 *    stops once nothing is populating.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

import FactIndexesPage, { type FactIndex } from "../pages/settings/indexes";

const authProvider = {
  login: vi.fn(async () => ({ success: true })),
  logout: vi.fn(async () => ({ success: true })),
  check: vi.fn(async () => ({ authenticated: true })),
  onError: vi.fn(async () => ({})),
  getPermissions: vi.fn(async () => ({ authorityLevel: 90 })),
  getIdentity: vi.fn(async () => ({ id: "act-1", authorityLevel: 90 })),
} as any;

const notificationProvider = {
  open: vi.fn(),
  close: vi.fn(),
} as any;

const dataProvider = {
  getApiUrl: () => "http://test.local",
  getList: vi.fn(async () => ({ data: [], total: 0 })),
  getOne: vi.fn(async () => ({ data: { id: "x" } })),
  getMany: vi.fn(async () => ({ data: [] })),
  create: vi.fn(async () => ({ data: { id: "x" } })),
  update: vi.fn(async () => ({ data: { id: "x" } })),
  deleteOne: vi.fn(async () => ({ data: { id: "x" } })),
  custom: vi.fn(async () => ({ data: {} })),
} as any;

const resources = [{ name: "fact-indexes", list: "/settings/indexes" }];

function renderPage(pollIntervalMs?: number) {
  return render(
    <MemoryRouter initialEntries={["/settings/indexes"]}>
      <Refine
        routerProvider={routerBindings}
        dataProvider={dataProvider}
        authProvider={authProvider}
        notificationProvider={notificationProvider}
        resources={resources}
      >
        <FactIndexesPage pollIntervalMs={pollIntervalMs} />
      </Refine>
    </MemoryRouter>,
  );
}

/** Build one catalog row with overrides. */
function row(name: string, overrides: Partial<FactIndex> = {}): FactIndex {
  return {
    name,
    property: name.replace(/^eb_fact_/, ""),
    description: `Description for ${name}`,
    exists: false,
    state: null,
    population_percent: null,
    ...overrides,
  };
}

/** All /admin/indexes* fetch calls so far as [method, pathname] pairs. */
function indexCalls(fetchMock: ReturnType<typeof vi.fn>): Array<[string, string]> {
  return fetchMock.mock.calls
    .map((c) => {
      const url = new URL(String(c[0]));
      const method = String((c[1] as RequestInit | undefined)?.method ?? "GET");
      return [method, url.pathname] as [string, string];
    })
    .filter(([, path]) => path.includes("/admin/indexes"));
}

/** Stub `fetch` to answer GET /admin/indexes from a queue (last one sticks). */
function stubFetch(statusPages: FactIndex[][]) {
  let getCount = 0;
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const method = String(init?.method ?? "GET");
    const path = new URL(String(input)).pathname;
    if (method === "GET" && path.endsWith("/admin/indexes")) {
      const page =
        statusPages[Math.min(getCount, statusPages.length - 1)];
      getCount += 1;
      return {
        ok: true,
        json: async () => ({ indexes: page }),
        text: async () => JSON.stringify({ indexes: page }),
      };
    }
    // Mutations (POST/DELETE/rebuild) — echo the backend's status envelope.
    return {
      ok: true,
      json: async () => ({ index: path.split("/").pop(), status: "ok" }),
      text: async () => JSON.stringify({ status: "ok" }),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("FactIndexesPage", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders one row per catalog index with the live state chips", async () => {
    const fetchMock = stubFetch([
      [
        row("eb_fact_gateway_id", {
          exists: true,
          state: "ONLINE",
          population_percent: 100,
        }),
        row("eb_fact_created_at", {
          exists: true,
          state: "POPULATING",
          population_percent: 42.5,
        }),
        row("eb_fact_confidence", {
          exists: true,
          state: "FAILED",
          population_percent: 0,
        }),
        row("eb_fact_scope"),
        row("eb_fact_memory_class"),
      ],
    ]);
    renderPage();

    expect(await screen.findByText("eb_fact_gateway_id")).toBeInTheDocument();
    expect(screen.getByText("Online")).toBeInTheDocument();
    expect(screen.getByText("Populating 43%")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    // The two absent indexes both render the outlined "Off" chip.
    expect(screen.getAllByText("Off")).toHaveLength(2);
    expect(
      screen.getByText("Description for eb_fact_scope"),
    ).toBeInTheDocument();
    // Rendering must never create anything implicitly — reads only.
    expect(indexCalls(fetchMock).every(([m]) => m === "GET")).toBe(true);
  });

  it("enabling an absent index POSTs /admin/indexes/{name}", async () => {
    const fetchMock = stubFetch([
      [row("eb_fact_scope")],
      [row("eb_fact_scope", { exists: true, state: "POPULATING", population_percent: 0 })],
    ]);
    renderPage();

    const toggle = await screen.findByLabelText("Enable eb_fact_scope");
    expect((toggle as HTMLInputElement).checked).toBe(false); // default OFF
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(indexCalls(fetchMock)).toContainEqual([
        "POST",
        "/admin/indexes/eb_fact_scope",
      ]);
    });
  });

  it("disabling an existing index DELETEs /admin/indexes/{name}", async () => {
    const fetchMock = stubFetch([
      [row("eb_fact_scope", { exists: true, state: "ONLINE", population_percent: 100 })],
      [row("eb_fact_scope")],
    ]);
    renderPage();

    const toggle = await screen.findByLabelText("Enable eb_fact_scope");
    expect((toggle as HTMLInputElement).checked).toBe(true);
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(indexCalls(fetchMock)).toContainEqual([
        "DELETE",
        "/admin/indexes/eb_fact_scope",
      ]);
    });
  });

  it("rebuild asks for confirmation, then POSTs .../rebuild", async () => {
    const fetchMock = stubFetch([
      [row("eb_fact_scope", { exists: true, state: "ONLINE", population_percent: 100 })],
    ]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Rebuild" }));
    // Confirm dialog — nothing has been sent yet.
    expect(
      await screen.findByText("Rebuild index?"),
    ).toBeInTheDocument();
    expect(
      indexCalls(fetchMock).filter(([m]) => m !== "GET"),
    ).toHaveLength(0);

    // The dialog's warning-coloured action shares the "Rebuild" name; pick the
    // one inside the dialog.
    const buttons = screen.getAllByRole("button", { name: "Rebuild" });
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(indexCalls(fetchMock)).toContainEqual([
        "POST",
        "/admin/indexes/eb_fact_scope/rebuild",
      ]);
    });
  });

  it("cancelling the rebuild dialog sends nothing", async () => {
    const fetchMock = stubFetch([
      [row("eb_fact_scope", { exists: true, state: "ONLINE", population_percent: 100 })],
    ]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Rebuild" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByText("Rebuild index?")).not.toBeInTheDocument();
    });
    expect(indexCalls(fetchMock).filter(([m]) => m !== "GET")).toHaveLength(0);
  });

  it("surfaces a fetch error (e.g. 503 context-only) as an alert", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({
        detail: "Memory store not available (context-only deployment)",
      }),
      text: async () =>
        JSON.stringify({
          detail: "Memory store not available (context-only deployment)",
        }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    expect(
      await screen.findByText(
        "Memory store not available (context-only deployment)",
      ),
    ).toBeInTheDocument();
  });

  it("polls while POPULATING and stops once the index is ONLINE", async () => {
    const fetchMock = stubFetch([
      [row("eb_fact_scope", { exists: true, state: "POPULATING", population_percent: 10 })],
      [row("eb_fact_scope", { exists: true, state: "POPULATING", population_percent: 80 })],
      [row("eb_fact_scope", { exists: true, state: "ONLINE", population_percent: 100 })],
    ]);
    renderPage(25); // fast poll cadence for the test

    // Two poll cycles land: 10% -> 80% -> ONLINE.
    expect(await screen.findByText("Populating 10%")).toBeInTheDocument();
    expect(await screen.findByText("Populating 80%")).toBeInTheDocument();
    expect(await screen.findByText("Online")).toBeInTheDocument();

    // Once ONLINE, polling stops — the GET count stays flat.
    const settled = indexCalls(fetchMock).filter(([m]) => m === "GET").length;
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(
      indexCalls(fetchMock).filter(([m]) => m === "GET"),
    ).toHaveLength(settled);
  });
});
