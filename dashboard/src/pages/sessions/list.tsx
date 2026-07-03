// Sessions list page.
//
// Active tab reads the active_sessions Redis SET (enriched with a trace
// summary — ActiveSessionSummary: session_key, session_id, event_count,
// last_event_at). Recent tab reads the trace ledger's session listing for the
// selected range (SessionListItem: session_id, session_key, first_event_at,
// last_event_at, event_count).
//
// Rows navigate to the detail page by session_id (the UUID that the
// /trace/session/{id} and /working-set/{id} endpoints require) and carry the
// human-readable session_key in the query string for display (sessions-1/2).

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
import { apiGet, TIME_RANGES, type TimeRange } from "../home/dashboardApi";
import { formatRelativeTime } from "../../lib/format";
import { errorMessage } from "../../lib/errors";

// Backend caps /dashboard/sessions/recent at this many rows per range
// (dashboard.py: limit Query default). We request it explicitly and surface a
// truncation note when the response fills it (sessions-6).
const RECENT_LIMIT = 100;

interface SessionRow {
  // Identity
  session_key?: string;
  key?: string;
  session_id?: string;
  // Enrichment the sessions endpoints actually return
  agent_name?: string;
  event_count?: number;
  first_event_at?: string;
  last_event_at?: string;
}

function sessionKey(s: SessionRow): string {
  return String(s.session_key ?? s.key ?? "");
}
function sessionId(s: SessionRow): string {
  return String(s.session_id ?? "");
}
function agentName(s: SessionRow): string {
  if (s.agent_name) return s.agent_name;
  const k = sessionKey(s);
  const parts = k.split(":");
  return parts.length >= 2 ? parts[1] : k;
}

/** Duration between first and last event (Recent rows carry both). */
function fmtRange(first?: string, last?: string): string {
  if (!first || !last) return "—";
  const a = new Date(first).getTime();
  const b = new Date(last).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return "—";
  const secs = Math.max(0, Math.round((b - a) / 1000));
  const m = Math.floor(secs / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return `${secs}s`;
}

function TimeCell({ iso }: { iso?: string }) {
  const { text, title } = formatRelativeTime(iso);
  return <span title={title}>{text}</span>;
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
      const res = await apiGet<any>(
        path,
        tab === 1 ? { time_range: range, limit: RECENT_LIMIT } : undefined,
      );
      let items: SessionRow[] = Array.isArray(res)
        ? res
        : (res.sessions ?? res.items ?? []);
      // active endpoint may return bare string keys
      items = items.map((s) =>
        typeof s === "string" ? ({ session_key: s } as SessionRow) : s,
      );
      setRows(items);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [tab, range]);

  useEffect(() => {
    void load();
  }, [load]);

  const openSession = (s: SessionRow) => {
    const sid = sessionId(s);
    const sk = sessionKey(s);
    // Navigate by session_id (what the detail endpoints need). Carry the
    // session_key in the query string so the detail header can show it. If a
    // row has no session_id (e.g. ledger enrichment unavailable), fall back to
    // the key so the URL is still meaningful.
    if (sid) {
      const q = sk ? `?session_key=${encodeURIComponent(sk)}` : "";
      push(`/sessions/${encodeURIComponent(sid)}${q}`);
    } else if (sk) {
      push(`/sessions/${encodeURIComponent(sk)}`);
    }
  };

  const isRecent = tab === 1;
  const colCount = isRecent ? 6 : 4;
  const truncated = isRecent && rows.length >= RECENT_LIMIT;

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
        {isRecent && (
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
                <TableCell align="right">Events</TableCell>
                {isRecent ? (
                  <>
                    <TableCell>Started</TableCell>
                    <TableCell>Ended</TableCell>
                    <TableCell>Duration</TableCell>
                  </>
                ) : (
                  <TableCell>Last event</TableCell>
                )}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((s, i) => (
                <TableRow
                  key={sessionId(s) || sessionKey(s) || i}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() => openSession(s)}
                >
                  <TableCell>{sessionKey(s)}</TableCell>
                  <TableCell>{agentName(s)}</TableCell>
                  <TableCell align="right">{s.event_count ?? 0}</TableCell>
                  {isRecent ? (
                    <>
                      <TableCell>
                        <TimeCell iso={s.first_event_at} />
                      </TableCell>
                      <TableCell>
                        <TimeCell iso={s.last_event_at} />
                      </TableCell>
                      <TableCell>
                        {fmtRange(s.first_event_at, s.last_event_at)}
                      </TableCell>
                    </>
                  ) : (
                    <TableCell>
                      <TimeCell iso={s.last_event_at} />
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={colCount}>No sessions.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {truncated && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
        >
          Showing the {RECENT_LIMIT} most recent sessions for this range. Older
          sessions are not listed — narrow the time range to see fewer, more
          recent ones.
        </Typography>
      )}
    </Box>
  );
};

export default SessionsPage;
