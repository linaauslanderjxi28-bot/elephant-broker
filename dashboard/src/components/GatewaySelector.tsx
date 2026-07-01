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

import { useEffect, useState, type FC } from "react";
import { usePermissions } from "@refinedev/core";
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
} from "../providers/apiClient";

/** Sentinel value for the "all gateways" (unscoped) option. */
const ALL_GATEWAYS = "__all__";
const ALL_GATEWAYS_MIN_AUTHORITY = 90;

interface GatewayOption {
  id: string;
  label: string;
}

interface DashboardPreferences {
  theme?: string;
  items_per_page?: number;
  selected_gateway?: string;
  preferences_json?: Record<string, unknown>;
  [key: string]: unknown;
}

/** Coerce the `/dashboard/gateways` payload into a stable option list. */
function normalizeGateways(payload: unknown): GatewayOption[] {
  const raw: unknown[] = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object"
      ? ((payload as Record<string, unknown>).gateways as unknown[]) ??
        ((payload as Record<string, unknown>).items as unknown[]) ??
        []
      : [];

  const options: GatewayOption[] = [];
  for (const entry of raw ?? []) {
    if (typeof entry === "string") {
      options.push({ id: entry, label: entry });
    } else if (entry && typeof entry === "object") {
      const obj = entry as Record<string, unknown>;
      const id = String(obj.gateway_id ?? obj.id ?? obj.name ?? "");
      if (!id) continue;
      const label = String(obj.name ?? obj.label ?? id);
      options.push({ id, label });
    }
  }
  return options;
}

export interface GatewaySelectorProps {
  /** Optional callback fired after a successful selection change. */
  onChange?: (gatewayId: string) => void;
}

export const GatewaySelector: FC<GatewaySelectorProps> = ({ onChange }) => {
  const { data: permissions } = usePermissions<{ authorityLevel: number }>();
  const authorityLevel = permissions?.authorityLevel ?? 0;
  const canSelectAll = authorityLevel >= ALL_GATEWAYS_MIN_AUTHORITY;

  const [gateways, setGateways] = useState<GatewayOption[]>([]);
  // Represent the empty/default selection with the ALL sentinel in the UI.
  const [selected, setSelected] = useState<string>(
    getSelectedGateway() || ALL_GATEWAYS,
  );
  const [loading, setLoading] = useState<boolean>(true);

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
      const first = gateways[0].id;
      setSelectedGateway(first);
      setSelected(first);
    }
  }, [loading, canSelectAll, selected, gateways]);

  const handleChange = async (event: SelectChangeEvent<string>) => {
    const raw = event.target.value;
    const gatewayId = raw === ALL_GATEWAYS ? "" : raw;

    setSelected(raw);
    // Update the module store (persists to localStorage + broadcasts event so
    // the data provider and open views pick up the new scope).
    setSelectedGateway(gatewayId);

    // Persist to server-side preferences (best-effort; UI already updated).
    try {
      await apiClient.put("/dashboard/preferences", {
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
          <MenuItem key={gw.id} value={gw.id}>
            {gw.label}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
};

export default GatewaySelector;
