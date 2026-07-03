/**
 * accessControlProvider.test.ts — unit tests for the branch-new authority-based
 * Refine AccessControlProvider (`dashboard/src/accessControlProvider.ts`).
 *
 * The auth provider is mocked so these tests exercise ONLY the threshold logic
 * inside `can({ resource, action, params })` — no network, no SuperTokens, no
 * `/auth/identity` fetch. We drive the caller's authority level purely through
 * the mocked `authProvider.getPermissions()` and assert the per-(resource,
 * action) allow/deny decision, mirroring the mocking style of
 * `authProvider.test.ts` in this same directory.
 *
 * Covers the Phase 11 behavior: reads (list/show) require authority >= 70,
 * writes/config (delete, guard CRUD, effective-config, authority-rules) require
 * >= 90, below-threshold callers are denied with a reason, and the default /
 * anonymous (level 0) caller is denied every restricted action.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const getPermissions = vi.fn();

vi.mock("../providers/authProvider", () => ({
  authProvider: {
    getPermissions: (...args: unknown[]) => getPermissions(...args),
  },
}));

import {
  accessControlProvider,
  invalidateAuthorityCache,
} from "../accessControlProvider";

type CanArgs = Parameters<NonNullable<typeof accessControlProvider.can>>[0];

/**
 * Resolve `can()` for a caller whose authority level is `level`.
 * Invalidates the in-module 5s cache first so each call reflects the level we
 * just set (the provider caches the resolved level across calls otherwise).
 */
async function canAtLevel(level: number, args: CanArgs) {
  invalidateAuthorityCache();
  getPermissions.mockResolvedValue({ authorityLevel: level });
  return accessControlProvider.can!(args);
}

beforeEach(() => {
  vi.clearAllMocks();
  invalidateAuthorityCache();
  // Default: an anonymous / level-0 caller unless a test overrides it.
  getPermissions.mockResolvedValue({ authorityLevel: 0 });
});

describe("accessControlProvider.can — reads require authority >= 70", () => {
  it("allows a registered read (actors:list) at exactly the 70 threshold", async () => {
    const res = await canAtLevel(70, { resource: "actors", action: "list" });
    expect(res).toEqual({ can: true });
  });

  it("allows a registered read (actors:list) above the threshold (90)", async () => {
    const res = await canAtLevel(90, { resource: "actors", action: "list" });
    expect(res.can).toBe(true);
  });

  it("denies a registered read (actors:list) one below the threshold (69)", async () => {
    const res = await canAtLevel(69, { resource: "actors", action: "list" });
    expect(res.can).toBe(false);
    expect((res as any).reason).toBe(
      "Requires authority level 70 (you have 69).",
    );
  });

  it("applies the list=70 action default to an unregistered resource", async () => {
    const allowed = await canAtLevel(70, {
      resource: "something-new",
      action: "list",
    });
    expect(allowed.can).toBe(true);

    const denied = await canAtLevel(69, {
      resource: "something-new",
      action: "list",
    });
    expect(denied.can).toBe(false);
  });

  it("applies the show=70 action default to an unregistered resource", async () => {
    const allowed = await canAtLevel(70, {
      resource: "something-new",
      action: "show",
    });
    expect(allowed.can).toBe(true);

    const denied = await canAtLevel(50, {
      resource: "something-new",
      action: "show",
    });
    expect(denied.can).toBe(false);
  });
});

describe("accessControlProvider.can — writes/config require authority >= 90", () => {
  it("allows guards:create at exactly 90 and denies at 89", async () => {
    const allowed = await canAtLevel(90, {
      resource: "guards",
      action: "create",
    });
    expect(allowed.can).toBe(true);

    const denied = await canAtLevel(89, {
      resource: "guards",
      action: "create",
    });
    expect(denied.can).toBe(false);
    expect((denied as any).reason).toBe(
      "Requires authority level 90 (you have 89).",
    );
  });

  it("requires 90 for guards:delete (a dashboard WRITE)", async () => {
    expect(
      (await canAtLevel(90, { resource: "guards", action: "delete" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(70, { resource: "guards", action: "delete" })).can,
    ).toBe(false);
  });

  it("requires 90 for memory:delete (GDPR delete)", async () => {
    expect(
      (await canAtLevel(90, { resource: "memory", action: "delete" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(89, { resource: "memory", action: "delete" })).can,
    ).toBe(false);
  });

  it("pins effective-config:list to 90 — the exact rule overrides the list=70 default", async () => {
    // A team-lead / org-admin (70) can read most lists, but NOT effective-config.
    const denied = await canAtLevel(70, {
      resource: "effective-config",
      action: "list",
    });
    expect(denied.can).toBe(false);
    expect((denied as any).reason).toBe(
      "Requires authority level 90 (you have 70).",
    );

    const allowed = await canAtLevel(90, {
      resource: "effective-config",
      action: "list",
    });
    expect(allowed.can).toBe(true);
  });

  it("requires 90 for authority-rules:edit", async () => {
    expect(
      (await canAtLevel(90, { resource: "authority-rules", action: "edit" }))
        .can,
    ).toBe(true);
    expect(
      (await canAtLevel(89, { resource: "authority-rules", action: "edit" }))
        .can,
    ).toBe(false);
  });

  it("requires 90 for consolidation:run", async () => {
    expect(
      (await canAtLevel(90, { resource: "consolidation", action: "run" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(70, { resource: "consolidation", action: "run" })).can,
    ).toBe(false);
  });

  it("applies the delete=90 action default to an unregistered resource", async () => {
    expect(
      (await canAtLevel(90, { resource: "widgets", action: "delete" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(89, { resource: "widgets", action: "delete" })).can,
    ).toBe(false);
  });
});

describe("accessControlProvider.can — create/edit defaults require authority >= 50", () => {
  it("allows goals:create at 50 and denies at 49", async () => {
    expect(
      (await canAtLevel(50, { resource: "goals", action: "create" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(49, { resource: "goals", action: "create" })).can,
    ).toBe(false);
  });

  it("applies the edit=50 action default to an unregistered resource", async () => {
    expect(
      (await canAtLevel(50, { resource: "widgets", action: "edit" })).can,
    ).toBe(true);
    expect(
      (await canAtLevel(49, { resource: "widgets", action: "edit" })).can,
    ).toBe(false);
  });
});

describe("accessControlProvider.can — default / anonymous callers are denied", () => {
  it("denies a restricted read when getPermissions returns null (no identity)", async () => {
    invalidateAuthorityCache();
    getPermissions.mockResolvedValue(null);
    const res = await accessControlProvider.can!({
      resource: "actors",
      action: "list",
    });
    expect(res.can).toBe(false);
    expect((res as any).reason).toBe(
      "Requires authority level 70 (you have 0).",
    );
  });

  it("treats a thrown getPermissions as level 0 (fail-closed) and denies", async () => {
    invalidateAuthorityCache();
    getPermissions.mockRejectedValue(new Error("401 unauthorized"));
    const res = await accessControlProvider.can!({
      resource: "guards",
      action: "create",
    });
    expect(res.can).toBe(false);
    expect((res as any).reason).toBe(
      "Requires authority level 90 (you have 0).",
    );
  });

  it("accepts a bare-number getPermissions result as the authority level", async () => {
    invalidateAuthorityCache();
    getPermissions.mockResolvedValue(70);
    const res = await accessControlProvider.can!({
      resource: "actors",
      action: "list",
    });
    expect(res.can).toBe(true);
  });
});

describe("accessControlProvider.can — unrestricted resources (threshold 0)", () => {
  it("allows api-keys:list for an anonymous caller WITHOUT resolving authority", async () => {
    invalidateAuthorityCache();
    const res = await accessControlProvider.can!({
      resource: "api-keys",
      action: "list",
    });
    expect(res).toEqual({ can: true });
    // min <= 0 short-circuits before ever reading the caller's authority level.
    expect(getPermissions).not.toHaveBeenCalled();
  });

  it("allows consolidation:list (explicitly pinned to 0) for a level-0 caller", async () => {
    const res = await canAtLevel(0, {
      resource: "consolidation",
      action: "list",
    });
    expect(res.can).toBe(true);
  });
});

describe("accessControlProvider.can — resolves the resource name from Refine's arguments", () => {
  it("reads the resource object off params.resource (.name) to key the rule", async () => {
    // Refine passes params.resource as the resolved IResourceItem object.
    const res = await canAtLevel(89, {
      action: "create",
      params: { resource: { name: "guards" } } as any,
    });
    expect(res.can).toBe(false);
    expect((res as any).reason).toBe(
      "Requires authority level 90 (you have 89).",
    );
  });

  it("params.resource overrides the top-level resource name", async () => {
    // <CanAccess resource="gateway" action="select-all"> flows through params.
    const denied = await canAtLevel(70, {
      resource: "memory",
      action: "select-all",
      params: { resource: "gateway" } as any,
    });
    expect(denied.can).toBe(false);
    expect((denied as any).reason).toBe(
      "Requires authority level 90 (you have 70).",
    );

    const allowed = await canAtLevel(90, {
      resource: "memory",
      action: "select-all",
      params: { resource: "gateway" } as any,
    });
    expect(allowed.can).toBe(true);
  });
});

describe("accessControlProvider — button access-control options", () => {
  it("hides unauthorized action buttons (enableAccessControl + hideIfUnauthorized)", () => {
    expect(accessControlProvider.options?.buttons).toEqual({
      enableAccessControl: true,
      hideIfUnauthorized: true,
    });
  });
});
