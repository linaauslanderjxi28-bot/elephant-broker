/**
 * ActorsListPage.test.tsx — page-level test for the Actors list page
 * (`src/pages/actors/list.tsx`). Renders the page inside the same minimal
 * Refine + router harness as MemoryBrowser.test.tsx (mocked auth/notification
 * providers) with a stubbed global `fetch`, and pins the server-side active
 * filtering contract: the roster request hides soft-deactivated actors by
 * default (`status=active`) and the "Show inactive" toggle (default off)
 * opts in to everything (`status=all`).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

import ActorsPage from "../pages/actors/list";

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

const resources = [{ name: "actors", list: "/actors", show: "/actors/:id" }];

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/actors"]}>
      <Refine
        routerProvider={routerBindings}
        dataProvider={dataProvider}
        authProvider={authProvider}
        notificationProvider={notificationProvider}
        resources={resources}
      >
        <ActorsPage />
      </Refine>
    </MemoryRouter>,
  );
}

/** All roster fetches issued so far (ignores unrelated requests). */
function actorFetchUrls(fetchMock: ReturnType<typeof vi.fn>): URL[] {
  return fetchMock.mock.calls
    .map((c) => new URL(String(c[0])))
    .filter((u) => u.pathname.endsWith("/dashboard/actors"));
}

describe("ActorsPage active filtering", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        actors: [
          {
            actor_id: "a1",
            display_name: "Alive",
            actor_type: "worker_agent",
            authority_level: 0,
            active: true,
          },
        ],
      }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchMock);
  });

  it("requests only active actors by default (status=active)", async () => {
    renderPage();

    await waitFor(() => {
      expect(actorFetchUrls(fetchMock).length).toBeGreaterThan(0);
    });
    const [url] = actorFetchUrls(fetchMock);
    expect(url.searchParams.get("status")).toBe("active");
  });

  it("passes status=all when the Show-inactive toggle is enabled", async () => {
    renderPage();

    // Wait for the default (active-only) load to land first.
    await waitFor(() => {
      expect(actorFetchUrls(fetchMock).length).toBeGreaterThan(0);
    });

    const toggle = await screen.findByLabelText("Show inactive");
    expect((toggle as HTMLInputElement).checked).toBe(false); // default off
    fireEvent.click(toggle);

    await waitFor(() => {
      const urls = actorFetchUrls(fetchMock);
      expect(urls[urls.length - 1].searchParams.get("status")).toBe("all");
    });
  });
});
