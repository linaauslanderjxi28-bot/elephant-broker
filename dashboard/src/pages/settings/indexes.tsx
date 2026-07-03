// Fact Indexes settings page (authority >= 90 — all four backend
// /admin/indexes routes are gated by the "manage_indexes" authority action,
// default level 90, including the read).
//
// Per-index management for the OPT-IN Neo4j fact indexes (Fix 5). Neo4j is the
// source of truth: the page renders whatever GET /admin/indexes reports live
// (SHOW INDEXES) and NEVER creates an index implicitly — create / drop /
// rebuild fire only on an explicit operator action. Newly created (or rebuilt)
// indexes back-fill in the background, so while any row reports POPULATING we
// re-poll the status endpoint on a modest interval and stop once nothing is
// populating.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { apiGet, apiSend, useAuthority } from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";

/** Default status re-poll cadence while an index is back-filling. */
export const POLL_INTERVAL_MS = 4_000;

/** One catalog entry as returned by GET /admin/indexes (backend contract). */
export interface FactIndex {
  name: string;
  property: string;
  description: string;
  exists: boolean;
  /** null when the index does not exist. */
  state: "ONLINE" | "POPULATING" | "FAILED" | null;
  /** 0-100 while populating; null when the index does not exist. */
  population_percent: number | null;
}

/** State chip: Off (absent) / Online / Populating N% / Failed. */
function StateChip({ row }: { row: FactIndex }) {
  if (!row.exists) {
    return <Chip size="small" variant="outlined" label="Off" />;
  }
  switch (row.state) {
    case "ONLINE":
      return <Chip size="small" color="success" label="Online" />;
    case "POPULATING": {
      const pct =
        typeof row.population_percent === "number"
          ? ` ${Math.round(row.population_percent)}%`
          : "";
      return <Chip size="small" color="info" label={`Populating${pct}`} />;
    }
    case "FAILED":
      return <Chip size="small" color="error" label="Failed" />;
    default:
      // Unknown state string from a future Neo4j version — show it verbatim.
      return <Chip size="small" label={row.state ?? "Unknown"} />;
  }
}

export const FactIndexesPage: React.FC<{
  /** Poll cadence override (tests); defaults to {@link POLL_INTERVAL_MS}. */
  pollIntervalMs?: number;
}> = ({ pollIntervalMs = POLL_INTERVAL_MS }) => {
  const authority = useAuthority();
  const [rows, setRows] = useState<FactIndex[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** Name of the index with an in-flight mutation (disables its controls). */
  const [busy, setBusy] = useState<string | null>(null);
  const [rebuildTarget, setRebuildTarget] = useState<FactIndex | null>(null);

  // `background` skips the page spinner so polling doesn't flash the table.
  const load = useCallback(async (background = false) => {
    if (!background) setLoading(true);
    setError(null);
    try {
      const res = await apiGet<{ indexes: FactIndex[] }>("/admin/indexes");
      setRows(res.indexes ?? []);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      if (!background) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  // Re-poll while any index is back-filling; the effect re-runs on every rows
  // update, so the interval clears itself once nothing reports POPULATING.
  const anyPopulating = rows.some((r) => r.exists && r.state === "POPULATING");
  useEffect(() => {
    if (!anyPopulating) return;
    const timer = window.setInterval(() => {
      void load(true);
    }, pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [anyPopulating, pollIntervalMs, load]);

  const toggle = async (row: FactIndex) => {
    setBusy(row.name);
    setError(null);
    try {
      if (row.exists) {
        await apiSend("DELETE", `/admin/indexes/${row.name}`);
      } else {
        await apiSend("POST", `/admin/indexes/${row.name}`);
      }
      await load(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const confirmRebuild = async () => {
    if (!rebuildTarget) return;
    const name = rebuildTarget.name;
    setRebuildTarget(null);
    setBusy(name);
    setError(null);
    try {
      await apiSend("POST", `/admin/indexes/${name}/rebuild`);
      await load(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  if (authority < 90) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="warning">
          Fact index management requires authority &ge; 90.
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Fact Indexes
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Opt-in (off by default): each enabled index speeds its queries but adds
        per-write maintenance cost, and is database-global — one index covers
        every gateway on this Neo4j host.
      </Typography>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Index</TableCell>
                <TableCell>Description</TableCell>
                <TableCell>State</TableCell>
                <TableCell align="center">Enabled</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.name}>
                  <TableCell>
                    <Typography
                      variant="body2"
                      sx={{
                        fontFamily:
                          "ui-monospace, SFMono-Regular, Menlo, monospace",
                      }}
                    >
                      {r.name}
                    </Typography>
                    <Typography variant="caption" color="text.disabled">
                      {r.property}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {r.description}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <StateChip row={r} />
                  </TableCell>
                  <TableCell align="center">
                    <Switch
                      checked={r.exists}
                      disabled={busy === r.name}
                      onChange={() => void toggle(r)}
                      inputProps={{ "aria-label": `Enable ${r.name}` }}
                    />
                  </TableCell>
                  <TableCell align="right">
                    <Button
                      size="small"
                      disabled={!r.exists || busy === r.name}
                      onClick={() => setRebuildTarget(r)}
                    >
                      Rebuild
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>No indexes in the catalog.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Dialog open={!!rebuildTarget} onClose={() => setRebuildTarget(null)}>
        <DialogTitle>Rebuild index?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Rebuild <strong>{rebuildTarget?.name}</strong>? The index is
            dropped and re-created — queries lose it until Neo4j finishes
            back-filling in the background.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRebuildTarget(null)}>Cancel</Button>
          <Button color="warning" variant="contained" onClick={confirmRebuild}>
            Rebuild
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default FactIndexesPage;
