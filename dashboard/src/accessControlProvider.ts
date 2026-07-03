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

/**
 * Authority thresholds keyed by `"resource:action"`, most specific first.
 *
 * Aligned to the backend enforcement matrix (RC-11): dashboard READ routes
 * require authority >= 70 (`READ = require_authority(70)`), and dashboard WRITE
 * routes (guard-rule CRUD, effective-config) require >= 90
 * (`WRITE = require_authority(90)`). Goal/procedure/actor creation flows POST to
 * `/admin/*`, whose authority is scope-dependent (backend AUTHORITY_DEFAULTS:
 * team goal 50, org goal 70, global goal 90); the single frontend threshold
 * mirrors the lowest meaningful band (50) and the per-scope UI restriction is
 * handled by `scopesForAuthority`. This gate is UX-only — the server
 * `require_authority` dependency is the enforcement boundary.
 */
const AUTHORITY_RULES: Record<string, number> = {
  // --- Memory ---
  "memory:edit": 50, // inline fact edit / promote
  "memory:promote-scope": 50,
  "memory:promote-class": 50,
  "memory:delete": 90, // GDPR delete (dashboard WRITE)
  "memory-graph:list": 70, // graph explorer view (backend READ = authority>=70)
  // --- Knowledge (create/edit POST to /admin/* with scope-based authority) ---
  "goals:create": 50,
  "goals:edit": 50,
  "procedures:create": 50,
  "procedures:edit": 50,
  // --- Actors & organizations ---
  "actors:list": 70, // backend READ
  "actors:edit": 70, // deactivate / mutate actor (admin authority)
  "organizations:list": 70,
  "organizations:edit": 70,
  // --- Runtime / guards ---
  "guards:create": 90, // create custom rule (dashboard WRITE)
  "guards:edit": 90, // edit rule / per-profile config (dashboard WRITE)
  "guards:delete": 90, // dashboard WRITE
  "guards:approvals": 70, // approval-queue tab (backend READ)
  // --- Runtime / consolidation ---
  // Consolidation reads carry no backend require_authority (any authenticated
  // caller may view reports/suggestions), so they are explicitly pinned to 0
  // to opt out of the list/show read default below.
  "consolidation:list": 0,
  "consolidation:run": 90, // trigger a consolidation ("sleep") run
  "consolidation:edit": 70, // approve / reject procedure suggestions
  // --- Runtime / trace ---
  "trace:list": 70, // trace explorer view (mirrors memory-graph:list)
  // --- Settings ---
  "api-keys:list": 0, // any authenticated caller manages their own keys
  "api-keys:create": 0,
  "api-keys:delete": 0,
  "authority-rules:list": 90,
  "authority-rules:edit": 90,
  "authority-rules:delete": 90,
  "effective-config:list": 90, // backend WRITE (settings-7)
  "fact-indexes:list": 90, // /admin/indexes read is also "manage_indexes" (90)
  "fact-indexes:edit": 90, // create / drop / rebuild an index
  // --- Cross-cutting UI affordances ---
  "gateway:select-all": 90, // GatewaySelector "all gateways" option
};

/**
 * Fallback thresholds by action when no `resource:action` rule matches.
 * Reads (`list`/`show`) require 70 to mirror the backend READ dependency, so
 * read pages hide for under-authority operators instead of 403-walling them.
 */
const ACTION_DEFAULTS: Record<string, number> = {
  list: 70,
  show: 70,
  delete: 90,
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

/**
 * Resolve the resource NAME from Refine's `can({ resource, params })` call.
 *
 * Refine passes `params.resource` as the resolved resource OBJECT (an
 * `IResourceItem` with a `.name`) for every menu item / `<CanAccess>` gate; the
 * top-level `resource` argument is the name string. The previous code read
 * `params.resource` as a string, so the object stringified to "[object Object]",
 * never matched a rule, and `can()` fell through to allow-everything (auth-1).
 * We now read `.name` off the object, then fall back to the string forms.
 */
function resolveResourceName(
  resource: unknown,
  params: { resource?: unknown } | undefined,
): string | undefined {
  const fromParams = params?.resource;
  if (fromParams && typeof fromParams === "object") {
    const name = (fromParams as { name?: unknown }).name;
    if (typeof name === "string" && name) return name;
  }
  if (typeof fromParams === "string" && fromParams) return fromParams;
  if (typeof resource === "string" && resource) return resource;
  if (resource && typeof resource === "object") {
    const name = (resource as { name?: unknown }).name;
    if (typeof name === "string" && name) return name;
  }
  return undefined;
}

export const accessControlProvider: AccessControlProvider = {
  can: async ({ resource, action, params }) => {
    // Resolve the resource name from Refine's resource object (params.resource)
    // or the string forms — never stringify the object into the rule key.
    const resourceName = resolveResourceName(resource, params);
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
