// Sessions list page.
//
// Active tab reads the active_sessions Redis SET (enriched with trace summary);
// Recent tab reads ClickHouse SESSION_BOUNDARY events for the selected range.

import React, { useCallback, useEffect, useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import {
  apiGet,
  relativeTime,
  TIME_RANGES,
  type TimeRange,
} from "../home/dashboardApi";

interface SessionRow {
  session_key?: string;
  key?: string;
  agent_name?: string;
  profile?: string;
  profile_name?: string;
  turn_count?: number;
  turns?: number;
  facts_extracted?: number;
  facts?: number;
  duration?: string | number;
  duration_seconds?: number;
  started_at?: string;
  ended_at?: string;
}

function sessionKey(s: SessionRow): string {
  return String(s.session_key ?? s.key ?? "");
}
function agentName(s: SessionRow): string {
  if (s.agent_name) return s.agent_name;
  const k = sessionKey(s);
  const parts = k.split(":");
  return parts.length >= 2 ? parts[1] : k;
}
function fmtDuration(s: SessionRow): string {
  const secs = s.duration_seconds ?? (typeof s.duration === "number" ? s.duration : undefined);
  if (secs == null) return String(s.duration ?? "—");
  const m = Math.floor(secs / 60);
  const h = Math.floor(m / 60);
  return h > 0 ? `${h}h${m % 60}m` : `${m}m`;
}

export const SessionsPage: React.FC = () => {
  const [tab, setTab] = useState(0);
  const [range, setRange] = useState<TimeRange>("24h");
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { push } = useNavigation();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const path =
        tab === 0
          ? "/dashboard/sessions/active"
          : "/dashboard/sessions/recent";
      const res = await apiGet<any>(path, tab === 1 ? { time_range: range } : undefined);
      let items: SessionRow[] = Array.isArray(res)
        ? res
        : (res.items ?? res.sessions ?? []);
      // active endpoint may return bare string keys
      items = items.map((s) =>
        typeof s === "string" ? ({ session_key: s } as SessionRow) : s,
      );
      setRows(items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [tab, range]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Sessions
      </Typography>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
      >
        <Tabs value={tab} onChange={(_, v) => setTab(v)}>
          <Tab label="Active" />
          <Tab label="Recent" />
        </Tabs>
        {tab === 1 && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={range}
            onChange={(_, v) => v && setRange(v as TimeRange)}
          >
            {TIME_RANGES.map((r) => (
              <ToggleButton key={r} value={r}>
                {r}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress sx={{ mt: 2 }} />
      ) : (
        <Paper variant="outlined" sx={{ mt: 2 }}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Session key</TableCell>
                <TableCell>Agent</TableCell>
                <TableCell>Profile</TableCell>
                <TableCell align="right">Turns</TableCell>
                <TableCell align="right">Facts</TableCell>
                {tab === 1 && <TableCell>Started</TableCell>}
                {tab === 1 && <TableCell>Ended</TableCell>}
                <TableCell>Duration</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((s) => (
                <TableRow
                  key={sessionKey(s)}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() =>
                    push(`/sessions/${encodeURIComponent(sessionKey(s))}`)
                  }
                >
                  <TableCell>{sessionKey(s)}</TableCell>
                  <TableCell>{agentName(s)}</TableCell>
                  <TableCell>{s.profile ?? s.profile_name ?? "—"}</TableCell>
                  <TableCell align="right">
                    {s.turn_count ?? s.turns ?? "—"}
                  </TableCell>
                  <TableCell align="right">
                    {s.facts_extracted ?? s.facts ?? "—"}
                  </TableCell>
                  {tab === 1 && (
                    <TableCell>{relativeTime(s.started_at)}</TableCell>
                  )}
                  {tab === 1 && (
                    <TableCell>{relativeTime(s.ended_at)}</TableCell>
                  )}
                  <TableCell>{fmtDuration(s)}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={tab === 1 ? 8 : 6}>
                    No sessions.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  );
};

export default SessionsPage;
