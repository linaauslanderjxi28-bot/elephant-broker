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
import { errorMessage } from "../lib/errors";

/** Shape returned by `GET /auth/identity`. */
export interface EbIdentity {
  actor_id: string;
  display_name: string;
  authority_level: number;
  org_id: string | null;
  type: string;
}

let _identityCache: EbIdentity | null = null;

/**
 * Canonical localStorage key for the operator's default landing page
 * (mirrors PREF_KEYS.defaultPage in pages/settings/preferences.tsx — kept as a
 * literal here to avoid a provider → page import). The Preferences page writes
 * it; login/register read it so the "default page" preference actually takes
 * effect (settings-3).
 */
const DEFAULT_PAGE_KEY = "eb:pref:default_page";

/** Resolve the post-auth landing page from the saved preference ("/" fallback). */
function resolveDefaultPage(): string {
  if (typeof window === "undefined" || !window.localStorage) return "/";
  try {
    const v = window.localStorage.getItem(DEFAULT_PAGE_KEY);
    if (v && v.startsWith("/")) return v;
  } catch {
    /* ignore storage failures */
  }
  return "/";
}

/**
 * Turn a SuperTokens FIELD_ERROR response into a clean, user-facing message.
 * Strips the raw field-id prefix (e.g. "password: too weak" → "too weak",
 * auth-6) and joins multiple field errors.
 */
function fieldErrorMessage(
  formFields: Array<{ id: string; error: string }> | undefined,
): string {
  const msg = (formFields ?? [])
    .map((f) => f.error)
    .filter(Boolean)
    .join("; ");
  return msg || "Please check the highlighted fields.";
}

/** Friendly text for a non-OK SuperTokens auth status (never a raw enum). */
function statusMessage(status: string, fallback: string): string {
  switch (status) {
    case "WRONG_CREDENTIALS_ERROR":
      return "Incorrect email or password";
    case "SIGN_IN_NOT_ALLOWED":
    case "SIGN_UP_NOT_ALLOWED":
      return "Sign-in is not allowed for this account. Contact an administrator.";
    case "EMAIL_ALREADY_EXISTS_ERROR":
      return "An account with this email already exists.";
    default:
      return fallback;
  }
}

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
    // Refine renders `error.name` as the notification TITLE and `error.message`
    // as its body — so the title is a friendly headline (never an internal name
    // like "LoginError", auth-4) and the body is a clean message.
    try {
      const response = await EmailPassword.signIn({
        formFields: [
          { id: "email", value: email },
          { id: "password", value: password },
        ],
      });

      if (response.status === "OK") {
        clearIdentityCache();
        return { success: true, redirectTo: resolveDefaultPage() };
      }

      const message =
        response.status === "FIELD_ERROR"
          ? fieldErrorMessage(response.formFields)
          : statusMessage(response.status, "Sign-in failed. Please try again.");
      return {
        success: false,
        error: { name: "Sign-in failed", message },
      };
    } catch (err) {
      return {
        success: false,
        error: { name: "Sign-in failed", message: errorMessage(err) },
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
        return { success: true, redirectTo: resolveDefaultPage() };
      }
      const message =
        response.status === "FIELD_ERROR"
          ? fieldErrorMessage(response.formFields)
          : statusMessage(response.status, "Sign-up failed. Please try again.");
      return {
        success: false,
        error: { name: "Sign-up failed", message },
      };
    } catch (err) {
      return {
        success: false,
        error: { name: "Sign-up failed", message: errorMessage(err) },
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
