/**
 * MemoryBrowser.test.tsx — page-level test for the Memory Browse page
 * (`src/pages/memory/list.tsx`, SOW page 2). Renders the page inside a minimal
 * Refine + router harness with mocked data/auth/notification providers and
 * asserts that mounting drives a `getList` against the `memory` resource (i.e.
 * the page wires the browse table through the data provider), plus covers the
 * page's pure label helpers.
 */

import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

import MemoryList from "../pages/memory/list";
import { factClassLabel, scopeLabel } from "../pages/memory/types";

function makeDataProvider(getList: ReturnType<typeof vi.fn>) {
  return {
    getApiUrl: () => "http://test.local",
    getList,
    getOne: vi.fn(async () => ({ data: { id: "x" } })),
    getMany: vi.fn(async () => ({ data: [] })),
    create: vi.fn(async () => ({ data: { id: "x" } })),
    update: vi.fn(async () => ({ data: { id: "x" } })),
    deleteOne: vi.fn(async () => ({ data: { id: "x" } })),
    custom: vi.fn(async () => ({ data: {} })),
  } as any;
}

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

const resources = [{ name: "memory", list: "/memory", show: "/memory/:id" }];

describe("MemoryBrowser page (pure helpers)", () => {
  it("labels an unknown memory class as itself and a known scope nicely", () => {
    // factClassLabel falls back to the raw value for unknown classes.
    expect(factClassLabel("totally_unknown")).toBe("totally_unknown");
    // scopeLabel returns a human-readable label for a known scope.
    expect(typeof scopeLabel("team")).toBe("string");
    expect(scopeLabel("team").length).toBeGreaterThan(0);
  });
});

describe("MemoryBrowser page (render)", () => {
  it("mounts and requests the memory list via the data provider", async () => {
    const getList = vi.fn(async () => ({
      data: [
        {
          id: "f1",
          text: "The sky is blue",
          memory_class: "semantic",
          scope: "team",
          confidence: 0.9,
          created_at: new Date().toISOString(),
        },
      ],
      total: 1,
    }));

    render(
      <MemoryRouter initialEntries={["/memory"]}>
        <Refine
          routerProvider={routerBindings}
          dataProvider={makeDataProvider(getList)}
          authProvider={authProvider}
          notificationProvider={notificationProvider}
          resources={resources}
          options={{ disableTelemetry: true, reactQuery: { devtoolConfig: false } }}
        >
          <MemoryList />
        </Refine>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(getList).toHaveBeenCalled();
    });

    const call = (getList.mock.calls as unknown as any[])[0]?.[0] as
      | { resource?: string }
      | undefined;
    expect(call?.resource).toBe("memory");
  });
});
