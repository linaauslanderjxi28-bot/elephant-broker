/**
 * Authority-based access control for the ElephantBroker dashboard.
 *
 * Refine calls `can({ resource, action, params })` for every menu item and every
 * `<CanAccess>` gate. We resolve the caller's authority level via the auth
 * provider's `getPermissions()` (which reads `/auth/identity`) and compare it
 * against the per-(resource, action) thresholds agreed in the Phase 11 SOW.
 *
 * Authority bands (SOW §11.1): 0-49 regular, 50-69 team lead, 70-89 org admin,
 * 90+ system admin. The backend `require_authority(min_level)` dependency is the
 * source of truth — this provider only mirrors those thresholds so the UI hides
 * controls the caller cannot use. A denial here is a courtesy, not the gate.
 */
import type { AccessControlProvider } from "@refinedev/core";

import { authProvider } from "./providers/authProvider";

/** Authority thresholds keyed by `"resource:action"`, most specific first. */
const AUTHORITY_RULES: Record<string, number> = {
  // --- Memory ---
  "memory:edit": 50, // inline fact edit / promote
  "memory:promote-scope": 50,
  "memory:promote-class": 50,
  "memory:delete": 70, // GDPR delete
  // --- Knowledge ---
  "goals:create": 50,
  "goals:edit": 50,
  "procedures:create": 50,
  "procedures:edit": 50,
  // --- Actors & organizations ---
  "actors:list": 50,
  "actors:edit": 70, // deactivate / mutate actor
  "organizations:list": 70,
  "organizations:edit": 70,
  // --- Runtime / guards ---
  "guards:create": 70, // create custom rule
  "guards:edit": 70, // edit rule / per-profile config
  "guards:delete": 70,
  "guards:approvals": 50, // approval-queue tab
  // --- Settings ---
  "api-keys:list": 0, // any authenticated caller manages their own keys
  "api-keys:create": 0,
  "api-keys:delete": 0,
  "authority-rules:list": 90,
  "authority-rules:edit": 90,
  "authority-rules:delete": 90,
  "effective-config:list": 70,
  // --- Cross-cutting UI affordances ---
  "gateway:select-all": 90, // GatewaySelector "all gateways" option
};

/** Fallback thresholds by action when no `resource:action` rule matches. */
const ACTION_DEFAULTS: Record<string, number> = {
  delete: 70,
  create: 50,
  edit: 50,
};

const CACHE_TTL_MS = 5_000;
let cachedLevel: number | null = null;
let cachedAt = 0;

async function resolveAuthorityLevel(): Promise<number> {
  const now = Date.now();
  if (cachedLevel !== null && now - cachedAt < CACHE_TTL_MS) {
    return cachedLevel;
  }
  let level = 0;
  try {
    const perms = (await authProvider.getPermissions?.()) as
      | { authorityLevel?: number }
      | number
      | null
      | undefined;
    if (typeof perms === "number") {
      level = perms;
    } else if (perms && typeof perms.authorityLevel === "number") {
      level = perms.authorityLevel;
    }
  } catch {
    level = 0;
  }
  cachedLevel = level;
  cachedAt = now;
  return level;
}

/** Invalidate the cached authority level (call on login/logout/gateway switch). */
export function invalidateAuthorityCache(): void {
  cachedLevel = null;
  cachedAt = 0;
}

function requiredLevel(resource: string | undefined, action: string): number {
  if (resource) {
    const exact = AUTHORITY_RULES[`${resource}:${action}`];
    if (exact !== undefined) return exact;
  }
  const byAction = ACTION_DEFAULTS[action];
  if (byAction !== undefined) return byAction;
  return 0; // read/list/show of unrestricted resources
}

export const accessControlProvider: AccessControlProvider = {
  can: async ({ resource, action, params }) => {
    // Allow callers to override the resource name via params.resource
    // (used by <CanAccess resource="gateway" action="select-all">).
    const resourceName = (params?.resource as string | undefined) ?? resource;
    const min = requiredLevel(resourceName, action);
    if (min <= 0) {
      return { can: true };
    }
    const level = await resolveAuthorityLevel();
    if (level >= min) {
      return { can: true };
    }
    return {
      can: false,
      reason: `Requires authority level ${min} (you have ${level}).`,
    };
  },
  options: {
    buttons: {
      // Hide (not just disable) unauthorized action buttons.
      enableAccessControl: true,
      hideIfUnauthorized: true,
    },
  },
};

export default accessControlProvider;
