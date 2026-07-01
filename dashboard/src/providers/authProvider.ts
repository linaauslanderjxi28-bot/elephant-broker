/**
 * authProvider.ts — Refine AuthProvider backed by SuperTokens (EmailPassword +
 * Session recipes) for the ElephantBroker dashboard.
 *
 * SuperTokens owns credential + session lifecycle (`/auth/*` routes). This
 * provider adapts those primitives to Refine's contract and layers the EB
 * actor identity (`/auth/identity`) on top so `getIdentity()` /
 * `getPermissions()` return the authority level that drives Refine `<CanAccess>`
 * UI gating.
 *
 * Notes:
 *  - `getPermissions()` returns `{ authorityLevel: number }`; pages compare it
 *    against the SOW thresholds (50 = promote/edit, 70 = delete/org view,
 *    90 = authority-rules / all-gateways).
 *  - Identity is cached in-module so repeated `<CanAccess>` checks don't refetch
 *    on every render; the cache is invalidated on login/logout.
 */

import type { AuthProvider } from "@refinedev/core";
import Session from "supertokens-auth-react/recipe/session";
import EmailPassword from "supertokens-auth-react/recipe/emailpassword";

import { apiClient, HttpError } from "./apiClient";

/** Shape returned by `GET /auth/identity`. */
export interface EbIdentity {
  actor_id: string;
  display_name: string;
  authority_level: number;
  org_id: string | null;
  type: string;
}

let _identityCache: EbIdentity | null = null;

/** Fetch (and memoise) the current actor's EB identity. */
async function fetchIdentity(force = false): Promise<EbIdentity | null> {
  if (_identityCache && !force) return _identityCache;
  try {
    const identity = await apiClient.get<EbIdentity>("/auth/identity");
    _identityCache = identity ?? null;
    return _identityCache;
  } catch {
    _identityCache = null;
    return null;
  }
}

function clearIdentityCache(): void {
  _identityCache = null;
}

export const authProvider: AuthProvider = {
  login: async ({ email, password }) => {
    try {
      const response = await EmailPassword.signIn({
        formFields: [
          { id: "email", value: email },
          { id: "password", value: password },
        ],
      });

      if (response.status === "OK") {
        clearIdentityCache();
        return { success: true, redirectTo: "/" };
      }

      if (response.status === "FIELD_ERROR") {
        const message = response.formFields
          .map((f) => `${f.id}: ${f.error}`)
          .join("; ");
        return {
          success: false,
          error: { name: "LoginError", message: message || "Invalid input" },
        };
      }

      // WRONG_CREDENTIALS_ERROR / SIGN_IN_NOT_ALLOWED / etc.
      return {
        success: false,
        error: {
          name: "LoginError",
          message:
            response.status === "WRONG_CREDENTIALS_ERROR"
              ? "Incorrect email or password"
              : String(response.status),
        },
      };
    } catch (err) {
      return {
        success: false,
        error: {
          name: "LoginError",
          message: err instanceof Error ? err.message : "Login failed",
        },
      };
    }
  },

  register: async ({ email, password }) => {
    try {
      const response = await EmailPassword.signUp({
        formFields: [
          { id: "email", value: email },
          { id: "password", value: password },
        ],
      });
      if (response.status === "OK") {
        clearIdentityCache();
        return { success: true, redirectTo: "/" };
      }
      if (response.status === "FIELD_ERROR") {
        const message = response.formFields
          .map((f) => `${f.id}: ${f.error}`)
          .join("; ");
        return {
          success: false,
          error: { name: "RegisterError", message: message || "Invalid input" },
        };
      }
      return {
        success: false,
        error: { name: "RegisterError", message: String(response.status) },
      };
    } catch (err) {
      return {
        success: false,
        error: {
          name: "RegisterError",
          message: err instanceof Error ? err.message : "Registration failed",
        },
      };
    }
  },

  logout: async () => {
    try {
      await Session.signOut();
    } catch {
      /* best-effort: still clear local state and redirect */
    }
    clearIdentityCache();
    return { success: true, redirectTo: "/login" };
  },

  check: async () => {
    try {
      const exists = await Session.doesSessionExist();
      if (exists) {
        return { authenticated: true };
      }
    } catch {
      /* fall through to unauthenticated */
    }
    return {
      authenticated: false,
      redirectTo: "/login",
      logout: true,
    };
  },

  onError: async (error) => {
    const statusCode =
      (error as HttpError | undefined)?.statusCode ??
      (error as any)?.status ??
      (error as any)?.response?.status;
    if (statusCode === 401) {
      clearIdentityCache();
      return { logout: true, redirectTo: "/login", error };
    }
    return {};
  },

  getIdentity: async () => {
    const identity = await fetchIdentity();
    if (!identity) return null;
    return {
      id: identity.actor_id,
      name: identity.display_name,
      authorityLevel: identity.authority_level,
      orgId: identity.org_id,
      type: identity.type,
    };
  },

  getPermissions: async () => {
    const identity = await fetchIdentity();
    return { authorityLevel: identity?.authority_level ?? 0 };
  },
};

export default authProvider;
