// Session detail page.
//
// Turn-by-turn timeline built from GET /trace/session/{id}/timeline plus a
// header from the summary endpoint. Turns expand to full event lists; events
// expand to payload JSON. Event-type filter chips and fact navigation.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigation, useParsed } from "@refinedev/core";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
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

// --- Working set snapshot inspector ------------------------------------------
//
// GET /working-set/{session_id} returns the cached budget-competition result
// (schemas/working_set.py:WorkingSetSnapshot). The snapshot is cache-backed and
// commonly 404s for ended/evicted sessions — that is a normal empty state.

interface WsScores {
  turn_relevance?: number;
  session_goal_relevance?: number;
  global_goal_relevance?: number;
  recency?: number;
  successful_use_prior?: number;
  confidence?: number;
  evidence_strength?: number;
  novelty?: number;
  redundancy_penalty?: number;
  contradiction_penalty?: number;
  cost_penalty?: number;
  final?: number;
}

interface WsItem {
  id: string;
  source_type: string;
  retrieval_source?: string | null;
  source_id: string;
  text: string;
  scores?: WsScores;
  token_size?: number;
  system_prompt_eligible?: boolean;
  must_inject?: boolean;
  evidence_ref_ids?: string[];
  confidence?: number;
  use_count?: number;
  successful_use_count?: number;
  category?: string;
}

interface WsSnapshot {
  snapshot_id?: string;
  session_id?: string;
  items?: WsItem[];
  token_budget?: number;
  tokens_used?: number;
  created_at?: string;
  weights_used?: Record<string, number>;
}

const SCORE_DIMENSIONS: Array<[keyof WsScores & string, string]> = [
  ["turn_relevance", "Turn relevance"],
  ["session_goal_relevance", "Session goal relevance"],
  ["global_goal_relevance", "Global goal relevance"],
  ["recency", "Recency"],
  ["successful_use_prior", "Successful use prior"],
  ["confidence", "Confidence"],
  ["evidence_strength", "Evidence strength"],
  ["novelty", "Novelty"],
  ["redundancy_penalty", "Redundancy penalty"],
  ["contradiction_penalty", "Contradiction penalty"],
  ["cost_penalty", "Cost penalty"],
];

function fmtScore(n: number | undefined | null): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(3) : "—";
}

function WorkingSetItemRow({
  item,
  weights,
}: {
  item: WsItem;
  weights: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const { push } = useNavigation();
  const isFact = item.source_type === "fact";
  return (
    <>
      <TableRow
        hover
        sx={{ cursor: "pointer", "& > td": { borderBottom: open ? "none" : undefined } }}
        onClick={() => setOpen(!open)}
      >
        <TableCell padding="checkbox">
          <IconButton size="small">
            {open ? <ExpandMoreIcon /> : <ChevronRightIcon />}
          </IconButton>
        </TableCell>
        <TableCell sx={{ maxWidth: 420 }}>
          <Typography variant="body2" noWrap title={item.text}>
            {item.text || "—"}
          </Typography>
        </TableCell>
        <TableCell>
          <Stack direction="row" spacing={0.5}>
            <Chip size="small" label={item.source_type} variant="outlined" />
            {item.retrieval_source && (
              <Chip size="small" label={item.retrieval_source} color="info" variant="outlined" />
            )}
            {item.must_inject && <Chip size="small" label="must inject" color="warning" />}
          </Stack>
        </TableCell>
        <TableCell align="right">
          <Typography variant="body2" fontWeight={600}>
            {fmtScore(item.scores?.final)}
          </Typography>
        </TableCell>
        <TableCell align="right">{item.token_size ?? "—"}</TableCell>
        <TableCell align="right">{fmtScore(item.confidence)}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={6} sx={{ py: 0, borderBottom: open ? undefined : "none" }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ pl: 6, py: 1 }}>
              <Stack direction="row" spacing={1} sx={{ mb: 1, flexWrap: "wrap" }} alignItems="center">
                <Chip
                  size="small"
                  variant="outlined"
                  label={`${isFact ? "fact" : item.source_type} ${String(item.source_id ?? "").slice(0, 8)}`}
                  onClick={
                    isFact
                      ? (e) => {
                          e.stopPropagation();
                          push(`/memory/${item.source_id}`);
                        }
                      : undefined
                  }
                />
                {item.category && (
                  <Chip size="small" variant="outlined" label={item.category} />
                )}
                {item.system_prompt_eligible && (
                  <Chip size="small" variant="outlined" label="system-prompt eligible" />
                )}
                <Typography variant="caption" color="text.secondary">
                  used {item.use_count ?? 0}× ({item.successful_use_count ?? 0} successful),{" "}
                  {item.evidence_ref_ids?.length ?? 0} evidence refs
                </Typography>
              </Stack>
              <Table size="small" sx={{ maxWidth: 560 }}>
                <TableHead>
                  <TableRow>
                    <TableCell>Dimension</TableCell>
                    <TableCell align="right">Raw</TableCell>
                    <TableCell align="right">Weight</TableCell>
                    <TableCell align="right">Weighted</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {SCORE_DIMENSIONS.map(([key, label]) => {
                    const raw = item.scores?.[key];
                    const w = weights[key];
                    const contrib =
                      typeof raw === "number" && typeof w === "number" ? raw * w : undefined;
                    return (
                      <TableRow key={key}>
                        <TableCell>{label}</TableCell>
                        <TableCell align="right">{fmtScore(raw)}</TableCell>
                        <TableCell align="right">{fmtScore(w)}</TableCell>
                        <TableCell align="right">{fmtScore(contrib)}</TableCell>
                      </TableRow>
                    );
                  })}
                  <TableRow>
                    <TableCell sx={{ fontWeight: 600 }}>Final score</TableCell>
                    <TableCell />
                    <TableCell />
                    <TableCell align="right" sx={{ fontWeight: 600 }}>
                      {fmtScore(item.scores?.final)}
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

type WsSortKey = "score" | "tokens" | "confidence";
type WsStatus = "idle" | "loading" | "loaded" | "empty" | "error";

function WorkingSetSection({ sessionId }: { sessionId: string }) {
  const [status, setStatus] = useState<WsStatus>("idle");
  const [snapshot, setSnapshot] = useState<WsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orderBy, setOrderBy] = useState<WsSortKey>("score");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const snap = await apiGet<WsSnapshot>(
        `/working-set/${encodeURIComponent(sessionId)}`,
      );
      setSnapshot(snap);
      setStatus("loaded");
    } catch (e) {
      const msg = (e as Error).message ?? "";
      if (msg.startsWith("404") || msg.startsWith("422")) {
        // Snapshots are cache-backed; ended/evicted sessions have none.
        setStatus("empty");
      } else if (msg.startsWith("501")) {
        setStatus("error");
        setError("Working set manager is not available on this runtime.");
      } else {
        setStatus("error");
        setError(msg || "Failed to load working set snapshot.");
      }
    }
  }, [sessionId]);

  const onExpand = (_: unknown, expanded: boolean) => {
    if (expanded && status === "idle") void load();
  };

  const sortKey = useCallback(
    (it: WsItem): number => {
      if (orderBy === "tokens") return it.token_size ?? 0;
      if (orderBy === "confidence") return it.confidence ?? 0;
      return it.scores?.final ?? 0;
    },
    [orderBy],
  );

  const items = useMemo(() => {
    const arr = [...(snapshot?.items ?? [])];
    arr.sort((a, b) =>
      order === "asc" ? sortKey(a) - sortKey(b) : sortKey(b) - sortKey(a),
    );
    return arr;
  }, [snapshot, order, sortKey]);

  const handleSort = (key: WsSortKey) => {
    if (orderBy === key) setOrder(order === "desc" ? "asc" : "desc");
    else {
      setOrderBy(key);
      setOrder("desc");
    }
  };

  const budget = snapshot?.token_budget ?? 0;
  const used = snapshot?.tokens_used ?? 0;
  const pct = budget > 0 ? Math.min(100, (used / budget) * 100) : 0;
  const weights = snapshot?.weights_used ?? {};

  const sortHeader = (key: WsSortKey, label: string) => (
    <TableSortLabel
      active={orderBy === key}
      direction={orderBy === key ? order : "desc"}
      onClick={() => handleSort(key)}
    >
      {label}
    </TableSortLabel>
  );

  return (
    <Accordion disableGutters variant="outlined" sx={{ mb: 2 }} onChange={onExpand}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="subtitle1">Working set</Typography>
        {status === "loaded" && (
          <Chip
            size="small"
            label={`${snapshot?.items?.length ?? 0} items · ${used}/${budget} tokens`}
            sx={{ ml: 1 }}
          />
        )}
      </AccordionSummary>
      <AccordionDetails>
        {status === "loading" && <CircularProgress size={24} sx={{ m: 1 }} />}

        {status === "empty" && (
          <Typography color="text.secondary">
            No working-set snapshot cached for this session. Snapshots exist only
            while a session's working set is cached — ended or evicted sessions
            have none.
          </Typography>
        )}

        {status === "error" && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => void load()}>
                Retry
              </Button>
            }
          >
            {error}
          </Alert>
        )}

        {status === "loaded" && snapshot && (
          <>
            <Box
              sx={{
                display: "grid",
                gap: 2,
                mb: 2,
                gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, 1fr)" },
              }}
            >
              {[
                ["Token budget", budget],
                ["Tokens used", `${used} (${pct.toFixed(0)}%)`],
                ["Items included", snapshot.items?.length ?? 0],
                ["Snapshot created", relativeTime(snapshot.created_at)],
              ].map(([label, value]) => (
                <Paper variant="outlined" sx={{ p: 1.5 }} key={String(label)}>
                  <Typography variant="caption" color="text.secondary">
                    {label}
                  </Typography>
                  <Typography variant="h6">{String(value ?? "—")}</Typography>
                </Paper>
              ))}
            </Box>
            <LinearProgress
              variant="determinate"
              value={pct}
              sx={{ mb: 2, height: 6, borderRadius: 1 }}
            />

            {items.length === 0 ? (
              <Typography color="text.secondary">
                Snapshot contains no items — nothing won the budget competition.
              </Typography>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell padding="checkbox" />
                      <TableCell>Text</TableCell>
                      <TableCell>Type</TableCell>
                      <TableCell align="right">{sortHeader("score", "Score")}</TableCell>
                      <TableCell align="right">{sortHeader("tokens", "Tokens")}</TableCell>
                      <TableCell align="right">
                        {sortHeader("confidence", "Confidence")}
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {items.map((it) => (
                      <WorkingSetItemRow key={it.id} item={it} weights={weights} />
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </>
        )}
      </AccordionDetails>
    </Accordion>
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

      <WorkingSetSection sessionId={sessionId} />

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
