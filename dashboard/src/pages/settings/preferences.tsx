// Preferences settings page.
//
// Reads/writes GET/PUT /dashboard/preferences (theme, items per page, default
// page, selected gateway). The selected gateway is mirrored to the shared
// gateway store (providers/apiClient + providers/gatewayKey) so every request
// helper scopes requests consistently.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { apiGet, apiSend } from "../home/dashboardApi";
import { setSelectedGateway } from "../../providers/apiClient";

interface Prefs {
  theme?: string;
  items_per_page?: number;
  default_page?: string;
  selected_gateway?: string;
}

const PAGES = ["/", "/memory", "/sessions", "/guards", "/actors"];

export const PreferencesPage: React.FC = () => {
  const [prefs, setPrefs] = useState<Prefs>({
    theme: "light",
    items_per_page: 50,
    default_page: "/",
    selected_gateway: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [gateways, setGateways] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<Prefs>("/dashboard/preferences");
      setPrefs((p) => ({ ...p, ...res }));
    } catch (e) {
      setError((e as Error).message);
    }
    try {
      const g = await apiGet<any>("/dashboard/gateways");
      setGateways(Array.isArray(g) ? g : (g.gateways ?? g.items ?? []));
    } catch {
      setGateways([]);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const set = (k: keyof Prefs, v: any) =>
    setPrefs((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setError(null);
    try {
      await apiSend("PUT", "/dashboard/preferences", prefs);
      // Update the shared gateway store (persists to the canonical
      // localStorage key and broadcasts the change so open views refetch).
      setSelectedGateway(prefs.selected_gateway ?? "");
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Box sx={{ p: 2, maxWidth: 520 }}>
      <Typography variant="h5" gutterBottom>
        Preferences
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <TextField
              select
              label="Theme"
              value={prefs.theme ?? "light"}
              onChange={(e) => set("theme", e.target.value)}
            >
              <MenuItem value="light">Light</MenuItem>
              <MenuItem value="dark">Dark</MenuItem>
            </TextField>
            <TextField
              select
              label="Items per page"
              value={prefs.items_per_page ?? 50}
              onChange={(e) => set("items_per_page", Number(e.target.value))}
            >
              {[25, 50, 100].map((n) => (
                <MenuItem key={n} value={n}>
                  {n}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Default page"
              value={prefs.default_page ?? "/"}
              onChange={(e) => set("default_page", e.target.value)}
            >
              {PAGES.map((p) => (
                <MenuItem key={p} value={p}>
                  {p}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Selected gateway"
              value={prefs.selected_gateway ?? ""}
              onChange={(e) => set("selected_gateway", e.target.value)}
            >
              <MenuItem value="">Default</MenuItem>
              {gateways.map((g) => (
                <MenuItem key={g} value={g}>
                  {g}
                </MenuItem>
              ))}
            </TextField>
            <Button variant="contained" onClick={save}>
              Save
            </Button>
          </Stack>
        </CardContent>
      </Card>
      <Snackbar
        open={saved}
        autoHideDuration={2000}
        onClose={() => setSaved(false)}
        message="Preferences saved"
      />
    </Box>
  );
};

export default PreferencesPage;
