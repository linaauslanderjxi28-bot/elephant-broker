/**
 * authProvider.test.ts — unit tests for the SuperTokens-backed Refine
 * AuthProvider. The SuperTokens recipes and the EB `apiClient` are mocked so the
 * tests exercise only the adapter logic (status mapping, identity caching,
 * permission thresholds) with no network or SDK side effects.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const signIn = vi.fn();
const signUp = vi.fn();
const signOut = vi.fn();
const doesSessionExist = vi.fn();
const apiGet = vi.fn();

vi.mock("supertokens-auth-react/recipe/emailpassword", () => ({
  default: {
    signIn: (...args: unknown[]) => signIn(...args),
    signUp: (...args: unknown[]) => signUp(...args),
  },
}));

vi.mock("supertokens-auth-react/recipe/session", () => ({
  default: {
    signOut: (...args: unknown[]) => signOut(...args),
    doesSessionExist: (...args: unknown[]) => doesSessionExist(...args),
  },
}));

vi.mock("../providers/apiClient", () => {
  class HttpError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
      super(message);
      this.name = "HttpError";
      this.statusCode = statusCode;
    }
  }
  return {
    apiClient: { get: (...args: unknown[]) => apiGet(...args) },
    HttpError,
  };
});

import { authProvider } from "../providers/authProvider";

beforeEach(async () => {
  vi.clearAllMocks();
  signOut.mockResolvedValue(undefined);
  doesSessionExist.mockResolvedValue(false);
  // Clearing the in-module identity cache between tests: logout does exactly that.
  await authProvider.logout?.({});
  vi.clearAllMocks();
  signOut.mockResolvedValue(undefined);
  doesSessionExist.mockResolvedValue(false);
});

describe("authProvider.login", () => {
  it("returns success and redirects home on OK", async () => {
    signIn.mockResolvedValue({ status: "OK" });
    const result = await authProvider.login({
      email: "a@b.com",
      password: "pw",
    });
    expect(result).toEqual({ success: true, redirectTo: "/" });
    expect(signIn).toHaveBeenCalledTimes(1);
  });

  it("maps WRONG_CREDENTIALS_ERROR to a friendly message", async () => {
    signIn.mockResolvedValue({ status: "WRONG_CREDENTIALS_ERROR" });
    const result = await authProvider.login({
      email: "a@b.com",
      password: "bad",
    });
    expect(result.success).toBe(false);
    expect((result as any).error.message).toBe("Incorrect email or password");
  });

  it("aggregates FIELD_ERROR messages", async () => {
    signIn.mockResolvedValue({
      status: "FIELD_ERROR",
      formFields: [{ id: "email", error: "invalid" }],
    });
    const result = await authProvider.login({ email: "x", password: "y" });
    expect(result.success).toBe(false);
    expect((result as any).error.message).toContain("email: invalid");
  });

  it("returns failure when the SDK throws", async () => {
    signIn.mockRejectedValue(new Error("network down"));
    const result = await authProvider.login({ email: "a", password: "b" });
    expect(result.success).toBe(false);
    expect((result as any).error.message).toBe("network down");
  });
});

describe("authProvider.check", () => {
  it("is authenticated when a session exists", async () => {
    doesSessionExist.mockResolvedValue(true);
    const result = await authProvider.check({});
    expect(result).toEqual({ authenticated: true });
  });

  it("redirects to /login when no session exists", async () => {
    doesSessionExist.mockResolvedValue(false);
    const result = await authProvider.check({});
    expect(result).toMatchObject({
      authenticated: false,
      redirectTo: "/login",
      logout: true,
    });
  });
});

describe("authProvider.logout", () => {
  it("signs out and redirects to /login", async () => {
    const result = await authProvider.logout({});
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ success: true, redirectTo: "/login" });
  });
});

describe("authProvider identity + permissions", () => {
  it("getPermissions returns the actor authority level", async () => {
    apiGet.mockResolvedValue({
      actor_id: "act-1",
      display_name: "Ada",
      authority_level: 70,
      org_id: "org-1",
      type: "human_coordinator",
    });
    const perms = (await authProvider.getPermissions?.({})) as {
      authorityLevel: number;
    };
    expect(perms.authorityLevel).toBe(70);
    expect(apiGet).toHaveBeenCalledWith("/auth/identity");
  });

  it("getPermissions defaults to 0 when identity fetch fails", async () => {
    apiGet.mockRejectedValue(new Error("401"));
    const perms = (await authProvider.getPermissions?.({})) as {
      authorityLevel: number;
    };
    expect(perms.authorityLevel).toBe(0);
  });

  it("getIdentity maps the EB identity payload", async () => {
    apiGet.mockResolvedValue({
      actor_id: "act-9",
      display_name: "Grace",
      authority_level: 90,
      org_id: null,
      type: "manager_agent",
    });
    const identity = (await authProvider.getIdentity?.()) as any;
    expect(identity).toMatchObject({
      id: "act-9",
      name: "Grace",
      authorityLevel: 90,
      orgId: null,
      type: "manager_agent",
    });
  });
});

describe("authProvider.onError", () => {
  it("triggers logout on 401", async () => {
    const result = await authProvider.onError({ statusCode: 401 } as any);
    expect(result).toMatchObject({ logout: true, redirectTo: "/login" });
  });

  it("is a no-op for non-401 errors", async () => {
    const result = await authProvider.onError({ statusCode: 500 } as any);
    expect(result).toEqual({});
  });
});
