/**
 * FactDetailClaims.test.tsx — behavior of the linked-claims panel on the Fact
 * Detail page (`src/pages/memory/show.tsx`). Each claim row is an Accordion:
 * expanding it lazily fetches `GET /claims/{id}` and renders the claim's
 * evidence receipts + (for a rejected claim) its rejection reason, so a reviewer
 * can SEE the evidence before hitting Verify/Reject.
 *
 * The data layer is mocked the same way sibling FE tests do it (a fake Refine
 * dataProvider whose `custom` handler routes by URL): the page detail load and
 * the per-claim GET both flow through `dataProvider.custom`.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import routerBindings from "@refinedev/react-router-v6";

import MemoryShow from "../pages/memory/show";

function makeFact() {
  return {
    id: "f1",
    text: "The sky is blue",
    category: "general",
    scope: "team",
    confidence: 0.9,
    memory_class: "semantic",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    use_count: 0,
    successful_use_count: 0,
  };
}

// Build a fake dataProvider whose `custom` routes by URL:
//  - `/dashboard/memory/f1/detail` -> the FactDetailResponse (claims list)
//  - `GET /claims/{id}`            -> the ClaimDetailResponse for that claim
//  - `POST /claims/{id}/verify`    -> a promoted status
// `claimDetails` maps claim_id -> ClaimDetailResponse.
function makeHarness(opts: {
  claims: Array<{ claim_id: string; claim_text: string; status: string; evidence_count: number }>;
  claimDetails: Record<string, unknown>;
  onCustom?: (args: { url: string; method: string }) => void;
}) {
  const custom = vi.fn(async (args: { url: string; method: string }) => {
    opts.onCustom?.(args);
    const { url, method } = args;
    if (url.endsWith("/detail")) {
      return {
        data: {
          fact: makeFact(),
          edges: [],
          claims: opts.claims,
          usage: {
            use_count: 0,
            successful_use_count: 0,
            success_rate: 0,
          },
        },
      };
    }
    const claimMatch = url.match(/\/claims\/([^/]+)$/);
    if (claimMatch && method === "get") {
      return { data: opts.claimDetails[claimMatch[1]] };
    }
    if (url.includes("/verify")) {
      return { data: { status: "supervisor_verified" } };
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
  const resources = [{ name: "memory", list: "/memory", show: "/memory/:id" }];

  render(
    <MemoryRouter initialEntries={["/memory/f1"]}>
      <Refine
        routerProvider={routerBindings}
        dataProvider={dataProvider}
        authProvider={authProvider}
        notificationProvider={notificationProvider}
        resources={resources}
        options={{ disableTelemetry: true, reactQuery: { devtoolConfig: false } }}
      >
        <Routes>
          <Route path="/memory/:id" element={<MemoryShow />} />
        </Routes>
      </Refine>
    </MemoryRouter>,
  );

  return { custom };
}

describe("Fact Detail claims panel", () => {
  it("lazily fetches /claims/{id} on expand and renders evidence rows (type + ref_value)", async () => {
    const { custom } = makeHarness({
      claims: [
        { claim_id: "c1", claim_text: "Deploy succeeded", status: "tool_supported", evidence_count: 2 },
      ],
      claimDetails: {
        c1: {
          claim_id: "c1",
          status: "tool_supported",
          evidence_refs: [
            {
              id: "e1",
              type: "tool_output",
              ref_value: "https://ci.example/run/42",
              created_at: new Date().toISOString(),
            },
            {
              id: "e2",
              type: "chunk_ref",
              ref_value: "chunk://abc123",
              created_at: new Date().toISOString(),
            },
          ],
        },
      },
    });

    // Panel renders the collapsed claim summary once the detail load resolves.
    await screen.findByText("Deploy succeeded");

    // Not fetched until expanded (lazy).
    const claimGetBefore = custom.mock.calls.filter(
      ([a]: any[]) => /\/claims\/c1$/.test(a.url) && a.method === "get",
    );
    expect(claimGetBefore.length).toBe(0);

    // Expand by clicking the summary (the claim text lives inside it).
    fireEvent.click(screen.getByText("Deploy succeeded"));

    // Evidence rows appear (humanized type chips + monospace ref_value).
    await screen.findByText("Tool Output");
    expect(screen.getByText("Chunk Ref")).toBeTruthy();
    expect(screen.getByText("https://ci.example/run/42")).toBeTruthy();
    expect(screen.getByText("chunk://abc123")).toBeTruthy();

    const claimGetAfter = custom.mock.calls.filter(
      ([a]: any[]) => /\/claims\/c1$/.test(a.url) && a.method === "get",
    );
    expect(claimGetAfter.length).toBe(1);
  });

  it("shows the rejection_reason for a rejected claim", async () => {
    makeHarness({
      claims: [
        { claim_id: "c2", claim_text: "Feature shipped", status: "rejected", evidence_count: 0 },
      ],
      claimDetails: {
        c2: {
          claim_id: "c2",
          status: "rejected",
          evidence_refs: [],
          rejection_reason: "No supporting deploy log was attached.",
        },
      },
    });

    fireEvent.click(await screen.findByText("Feature shipped"));

    expect(
      await screen.findByText("No supporting deploy log was attached."),
    ).toBeTruthy();
    expect(screen.getByText("Rejection reason")).toBeTruthy();
  });

  it("shows the empty state when a claim has no evidence", async () => {
    makeHarness({
      claims: [
        { claim_id: "c3", claim_text: "Unbacked claim", status: "unverified", evidence_count: 0 },
      ],
      claimDetails: {
        c3: { claim_id: "c3", status: "unverified", evidence_refs: [] },
      },
    });

    fireEvent.click(await screen.findByText("Unbacked claim"));

    expect(
      await screen.findByText("No evidence attached to this claim."),
    ).toBeTruthy();
  });

  it("clicking Verify does NOT expand the row but does call the verify endpoint", async () => {
    const { custom } = makeHarness({
      claims: [
        { claim_id: "c4", claim_text: "Verify me", status: "tool_supported", evidence_count: 1 },
      ],
      claimDetails: {
        c4: {
          claim_id: "c4",
          status: "tool_supported",
          evidence_refs: [
            { id: "e9", type: "tool_output", ref_value: "should-not-appear", created_at: null },
          ],
        },
      },
    });

    const verifyBtn = await screen.findByRole("button", { name: "Verify" });
    fireEvent.click(verifyBtn);

    // Verify endpoint was hit...
    await waitFor(() => {
      expect(
        custom.mock.calls.some(
          ([a]: any[]) => a.url.endsWith("/claims/c4/verify") && a.method === "post",
        ),
      ).toBe(true);
    });

    // ...but the accordion stayed collapsed: no lazy claim GET, no evidence body.
    const claimGet = custom.mock.calls.filter(
      ([a]: any[]) => /\/claims\/c4$/.test(a.url) && a.method === "get",
    );
    expect(claimGet.length).toBe(0);
    expect(screen.queryByText("should-not-appear")).toBeNull();
  });
});
