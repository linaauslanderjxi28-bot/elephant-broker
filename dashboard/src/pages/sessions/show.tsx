// Session detail page.
//
// Turn-by-turn timeline built from GET /trace/session/{id}/timeline plus a
// header from the summary endpoint. Turns expand to full event lists; events
// expand to payload JSON. Event-type filter chips and fact navigation.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigation, useParsed } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import {
  apiGet,
  downloadJson,
  eventChipColor,
  relativeTime,
  summarizeEvent,
  useAuthority,
} from "../home/dashboardApi";

interface TraceEvent {
  event_id?: string;
  id?: string;
  event_type: string;
  timestamp?: string;
  payload?: Record<string, any>;
}
interface TurnGroup {
  turn_index?: number;
  turn?: number;
  events: TraceEvent[];
}

function EventRow({ ev }: { ev: TraceEvent }) {
  const [open, setOpen] = useState(false);
  const { push } = useNavigation();
  const factIds: string[] = ev.payload?.fact_ids ?? [];
  return (
    <Box sx={{ pl: 2, py: 0.5 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <IconButton size="small" onClick={() => setOpen(!open)}>
          {open ? <ExpandMoreIcon /> : <ChevronRightIcon />}
        </IconButton>
        <Chip
          size="small"
          label={ev.event_type}
          color={eventChipColor(ev.event_type)}
        />
        <Typography variant="body2" sx={{ flex: 1 }}>
          {summarizeEvent(ev.event_type, ev.payload)}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {relativeTime(ev.timestamp)}
        </Typography>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pl: 6 }}>
          {factIds.length > 0 && (
            <Stack direction="row" spacing={1} sx={{ my: 0.5, flexWrap: "wrap" }}>
              {factIds.map((fid) => (
                <Chip
                  key={fid}
                  size="small"
                  variant="outlined"
                  label={`fact ${fid.slice(0, 8)}`}
                  onClick={() => push(`/memory/${fid}`)}
                />
              ))}
            </Stack>
          )}
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
            {JSON.stringify(ev.payload ?? {}, null, 2)}
          </pre>
        </Box>
      </Collapse>
    </Box>
  );
}

function TurnRow({
  turn,
  visibleTypes,
}: {
  turn: TurnGroup;
  visibleTypes: Set<string> | null;
}) {
  const [open, setOpen] = useState(false);
  const events = useMemo(
    () =>
      visibleTypes
        ? turn.events.filter((e) => visibleTypes.has(e.event_type))
        : turn.events,
    [turn.events, visibleTypes],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of turn.events) c[e.event_type] = (c[e.event_type] ?? 0) + 1;
    return c;
  }, [turn.events]);
  const summary = Object.entries(counts)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ");
  const idx = turn.turn_index ?? turn.turn ?? 0;

  return (
    <Paper variant="outlined" sx={{ mb: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        sx={{ p: 1, cursor: "pointer" }}
        onClick={() => setOpen(!open)}
      >
        <IconButton size="small">
          {open ? <ExpandMoreIcon /> : <ChevronRightIcon />}
        </IconButton>
        <Typography variant="subtitle2" sx={{ mr: 2 }}>
          Turn {idx}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {summary}
        </Typography>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pb: 1 }}>
          {events.map((e, i) => (
            <EventRow key={e.event_id ?? e.id ?? i} ev={e} />
          ))}
        </Box>
      </Collapse>
    </Paper>
  );
}

export const SessionShowPage: React.FC = () => {
  const { id } = useParsed();
  const sessionId = decodeURIComponent(String(id ?? ""));
  const authority = useAuthority();
  const [summary, setSummary] = useState<any>(null);
  const [turns, setTurns] = useState<TurnGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTypes, setActiveTypes] = useState<Set<string> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const enc = encodeURIComponent(sessionId);
      const [sum, tl] = await Promise.all([
        apiGet<any>(`/trace/session/${enc}/summary`).catch(() => null),
        apiGet<any>(`/trace/session/${enc}/timeline`).catch(() => null),
      ]);
      setSummary(sum);
      const groups: TurnGroup[] = Array.isArray(tl)
        ? tl
        : (tl?.turns ?? tl?.items ?? []);
      setTurns(groups);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) void load();
  }, [sessionId, load]);

  const allTypes = useMemo(() => {
    const s = new Set<string>();
    for (const t of turns) for (const e of t.events) s.add(e.event_type);
    return Array.from(s).sort();
  }, [turns]);

  const toggleType = (t: string) => {
    setActiveTypes((prev) => {
      const base = prev ? new Set(prev) : new Set(allTypes);
      if (base.has(t)) base.delete(t);
      else base.add(t);
      return base;
    });
  };

  if (loading) return <CircularProgress sx={{ m: 4 }} />;
  if (error) return <Alert severity="error">{error}</Alert>;

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">{sessionId}</Typography>
        {authority >= 50 && (
          <Button
            variant="outlined"
            onClick={() =>
              downloadJson(`session-${sessionId}.json`, { summary, turns })
            }
          >
            Export
          </Button>
        )}
      </Stack>

      {summary && (
        <Box
          sx={{
            display: "grid",
            gap: 2,
            mb: 2,
            gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, 1fr)" },
          }}
        >
          {[
            ["Agent", summary.agent_name ?? summary.session_key],
            ["Profile", summary.profile ?? summary.profile_name],
            ["Turns", summary.turn_count ?? summary.turns],
            ["Facts", summary.facts_extracted ?? summary.facts],
          ].map(([label, value]) => (
            <Paper variant="outlined" sx={{ p: 1.5 }} key={String(label)}>
              <Typography variant="caption" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h6">{String(value ?? "—")}</Typography>
            </Paper>
          ))}
        </Box>
      )}

      {allTypes.length > 0 && (
        <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: "wrap" }}>
          {allTypes.map((t) => {
            const on = !activeTypes || activeTypes.has(t);
            return (
              <Chip
                key={t}
                label={t}
                size="small"
                color={on ? eventChipColor(t) : "default"}
                variant={on ? "filled" : "outlined"}
                onClick={() => toggleType(t)}
              />
            );
          })}
        </Stack>
      )}

      {turns.length === 0 ? (
        <Typography color="text.secondary">No timeline events.</Typography>
      ) : (
        turns.map((t, i) => (
          <TurnRow
            key={t.turn_index ?? t.turn ?? i}
            turn={t}
            visibleTypes={activeTypes}
          />
        ))
      )}
    </Box>
  );
};

export default SessionShowPage;
