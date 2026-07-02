/**
 * gatewayKey.ts — single source of truth for the "selected gateway"
 * localStorage persistence.
 *
 * Historically two keys existed ("eb:selected_gateway" in apiClient and
 * "eb_selected_gateway" in dashboardApi), which let different pages silently
 * query different gateways. Every reader/writer must go through this module.
 *
 * Canonical key: `eb:selected_gateway`. On first read, if only the legacy
 * `eb_selected_gateway` key exists its value is adopted (migrated to the
 * canonical key) and the legacy key is removed.
 */

/** Canonical localStorage key for the selected gateway_id. */
export const SELECTED_GATEWAY_KEY = "eb:selected_gateway";

/** Legacy key (pre-consolidation). Read once for migration, then removed. */
export const LEGACY_SELECTED_GATEWAY_KEY = "eb_selected_gateway";

/**
 * Read the persisted gateway_id ("" => runtime default / unscoped).
 * Migrates the legacy key to the canonical one when only the legacy exists.
 * Safe under SSR/tests/private mode (returns "" on any storage failure).
 */
export function getStoredGateway(): string {
  if (typeof window === "undefined" || !window.localStorage) return "";
  try {
    const current = window.localStorage.getItem(SELECTED_GATEWAY_KEY);
    if (current !== null) return current;

    const legacy = window.localStorage.getItem(LEGACY_SELECTED_GATEWAY_KEY);
    if (legacy !== null) {
      // Adopt the legacy value under the canonical key, then drop the legacy
      // key so the two can never diverge again.
      if (legacy) {
        window.localStorage.setItem(SELECTED_GATEWAY_KEY, legacy);
      }
      window.localStorage.removeItem(LEGACY_SELECTED_GATEWAY_KEY);
      return legacy;
    }
    return "";
  } catch {
    return "";
  }
}

/**
 * Persist the gateway_id under the canonical key ("" removes it, meaning
 * runtime default). Always clears the legacy key so stale values cannot
 * resurface. Safe under SSR/tests/private mode.
 */
export function setStoredGateway(gatewayId: string): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    if (gatewayId) {
      window.localStorage.setItem(SELECTED_GATEWAY_KEY, gatewayId);
    } else {
      window.localStorage.removeItem(SELECTED_GATEWAY_KEY);
    }
    window.localStorage.removeItem(LEGACY_SELECTED_GATEWAY_KEY);
  } catch {
    /* ignore storage failures (private mode, quota) */
  }
}
