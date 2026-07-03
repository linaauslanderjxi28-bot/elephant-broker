/**
 * GatewaySelector.tsx — top-bar dropdown that selects which gateway's data the
 * dashboard views. The selection is:
 *   1. held in the module-level store (`setSelectedGateway`) so the data
 *      provider scopes every request to it, and
 *   2. persisted to the operator's server-side preferences
 *      (`PUT /dashboard/preferences`) so it survives across devices/sessions.
 *
 * The "All gateways" option (empty gateway_id => runtime default / unscoped) is
 * only offered to system admins (authority >= 90), per the SOW.
 */

import { useEffect, useRef, useState, type FC } from "react";
import { usePermissions, useInvalidate } from "@refinedev/core";
import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  type SelectChangeEvent,
} from "@mui/material";

import {
  apiClient,
  getSelectedGateway,
  setSelectedGateway,
  GATEWAY_CHANGED_EVENT,
} from "../providers/apiClient";
import { normalizeGateways, type SelectOption } from "../lib/apiNormalize";

/** Sentinel value for the "all gateways" (unscoped) option. */
const ALL_GATEWAYS = "__all__";
const ALL_GATEWAYS_MIN_AUTHORITY = 90;

interface DashboardPreferences {
  theme?: string;
  items_per_page?: number;
  selected_gateway?: string;
  preferences_json?: Record<string, unknown>;
  [key: string]: unknown;
}

// Gateway option normalization lives in lib/apiNormalize (single source of
// truth) so every gateway dropdown shares the same envelope handling — the
// duplicate previously inlined here has been removed.

export interface GatewaySelectorProps {
  /** Optional callback fired after a successful selection change. */
  onChange?: (gatewayId: string) => void;
}

export const GatewaySelector: FC<GatewaySelectorProps> = ({ onChange }) => {
  const { data: permissions } = usePermissions<{ authorityLevel: number }>();
  const authorityLevel = permissions?.authorityLevel ?? 0;
  const canSelectAll = authorityLevel >= ALL_GATEWAYS_MIN_AUTHORITY;

  const [gateways, setGateways] = useState<SelectOption[]>([]);
  // Represent the empty/default selection with the ALL sentinel in the UI.
  const [selected, setSelected] = useState<string>(
    getSelectedGateway() || ALL_GATEWAYS,
  );
  const [loading, setLoading] = useState<boolean>(true);

  // Refine's query-cache invalidator. Switching gateway must refetch every
  // mounted view under the new scope; the module store (getSelectedGateway) is
  // the single source of truth the data provider reads, but react-query keys do
  // not encode the gateway, so an explicit invalidation is what drives refetch.
  const invalidate = useInvalidate();

  // Track the gateway we last invalidated for so redundant broadcasts (e.g. the
  // mount-time hydration re-setting the already-current value) don't churn.
  const lastInvalidatedRef = useRef<string>(getSelectedGateway());

  // Drive query invalidation off the canonical GATEWAY_CHANGED_EVENT so EVERY
  // gateway change — this selector, the Preferences page, or any programmatic
  // setSelectedGateway — invalidates all data-provider queries and refetches
  // under the new scope. Fixes stale cross-tenant display on gateway switch.
  useEffect(() => {
    const handler = (event: Event) => {
      const next =
        (event as CustomEvent<string>).detail ?? getSelectedGateway();
      if (next === lastInvalidatedRef.current) return;
      lastInvalidatedRef.current = next;
      // `all` invalidates the whole data-provider query tree (every resource's
      // list/detail/many); active queries refetch immediately.
      void invalidate({ invalidates: ["all"] });
    };
    window.addEventListener(GATEWAY_CHANGED_EVENT, handler);
    return () => window.removeEventListener(GATEWAY_CHANGED_EVENT, handler);
  }, [invalidate]);

  // Load available gateways + persisted preference on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [gwPayload, prefs] = await Promise.all([
          apiClient
            .get("/dashboard/gateways")
            .catch(() => [] as unknown),
          apiClient
            .get<DashboardPreferences>("/dashboard/preferences")
            .catch(() => null),
        ]);
        if (cancelled) return;

        const options = normalizeGateways(gwPayload);
        setGateways(options);

        // Prefer an already-active in-memory selection, then server prefs.
        const persisted =
          getSelectedGateway() ||
          prefs?.selected_gateway ||
          (prefs?.preferences_json?.selected_gateway as string | undefined) ||
          "";
        if (persisted) {
          setSelectedGateway(persisted);
          setSelected(persisted);
        } else {
          setSelected(ALL_GATEWAYS);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Non-admins never see the "All gateways" sentinel, so if they have no
  // concrete selection default them to the first available gateway (keeps the
  // MUI Select value in range and scopes the data provider correctly).
  useEffect(() => {
    if (loading || canSelectAll) return;
    if (selected === ALL_GATEWAYS && gateways.length > 0) {
      const first = gateways[0].value;
      setSelectedGateway(first);
      setSelected(first);
    }
  }, [loading, canSelectAll, selected, gateways]);

  const handleChange = async (event: SelectChangeEvent<string>) => {
    const raw = event.target.value;
    const gatewayId = raw === ALL_GATEWAYS ? "" : raw;

    setSelected(raw);
    // Update the module store (persists to localStorage + broadcasts
    // GATEWAY_CHANGED_EVENT, which our listener above turns into a query-cache
    // invalidation so every open view refetches under the new scope).
    setSelectedGateway(gatewayId);

    // Persist to server-side preferences (best-effort; UI already updated).
    // `PUT /dashboard/preferences` is a FULL replace: the backend validates the
    // body into a UserPreferences model and writes every field, so a partial
    // body would reset theme / items_per_page / default_page to their defaults
    // (settings-4). Read-modify-write the latest prefs and change only the
    // gateway to preserve everything else.
    try {
      let current: DashboardPreferences = {};
      try {
        current =
          (await apiClient.get<DashboardPreferences>(
            "/dashboard/preferences",
          )) ?? {};
      } catch {
        /* if prefs can't be read, fall through to a gateway-only update */
      }
      await apiClient.put("/dashboard/preferences", {
        ...current,
        selected_gateway: gatewayId,
      });
    } catch {
      /* preference persistence is non-fatal */
    }

    onChange?.(gatewayId);
  };

  return (
    <FormControl size="small" sx={{ minWidth: 200 }} disabled={loading}>
      <InputLabel id="gateway-selector-label">Gateway</InputLabel>
      <Select
        labelId="gateway-selector-label"
        id="gateway-selector"
        label="Gateway"
        value={selected}
        onChange={handleChange}
      >
        {canSelectAll && (
          <MenuItem value={ALL_GATEWAYS}>All gateways</MenuItem>
        )}
        {gateways.map((gw) => (
          <MenuItem key={gw.value} value={gw.value}>
            {gw.label}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
};

export default GatewaySelector;
