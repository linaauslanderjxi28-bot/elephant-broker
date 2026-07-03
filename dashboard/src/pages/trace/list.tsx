// Trace Explorer (`/trace`) — generic cross-session trace event browser.
//
// Backend (elephantbroker/api/routes/trace.py, mounted at /trace):
//   GET  /trace/event-types   -> [{ type, description }]  (filter options)
//   POST /trace/query         -> TraceEvent[] (TraceQuery body: event_types,
//                                session_id, session_key, from_timestamp,
//                                to_timestamp, limit, offset). The middleware
//                                gateway_id always overrides the body value.
//   GET  /trace/{event_id}    -> single TraceEvent (detail drawer)
//
// PT-1: every /trace endpoint is rate-limited server-side (sliding window per
// gateway). This page therefore DEBOUNCES filter changes (500 ms) and never
// auto-polls — queries fire only on filter edits or an explicit Refresh.
//
// View is gated at authority >= 70 (org admin), mirroring memory-graph:list.
// The route-level <CanAccess> gate ("trace:list") is wired by the integration
// agent; the in-page check below is the courtesy fallback.

import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePermissions } from "@refinedev/core";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import RefreshIcon from "@mui/icons-material/Refresh";

import apiClient, { request } from "../../providers/apiClient";
import {
  eventChipColor,
  relativeTime,
  summarizeEvent,
  TIME_RANGES,
} from "../home/dashboardApi";

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

interface EventTypeInfo {
  type: string;
  description: string;
}

/** Mirror of schemas/trace.py TraceEvent (model_dump(mode="json")). */
interface TraceEvent {
  id: string;
  event_type: string;
  timestamp: string;
  session_id: string | null;
  session_key: string | null;
  gateway_id: string | null;
  agent_id: string | null;
  agent_key: string | null;
  parent_event_id: string | null;
  actor_ids?: string[];
  fact_ids?: string[];
  payload: Record<string, unknown>;
}

type RangeKey = (typeof TIME_RANGES)[number] | "all";

const RANGE_MS: Record<Exclude<RangeKey, "all">, number> = {
  "1h": 3_600_000,
  "6h": 6 * 3_600_000,
  "24h": 24 * 3_600_000,
  "7d": 7 * 86_400_000,
};

const LIMIT_OPTIONS = [50, 100, 250, 500, 1000];

/** TraceQuery.session_id is a UUID — validate before sending to avoid a 422. */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** PT-1: debounce filter-driven queries so typing can't burn the rate budget. */
const QUERY_DEBOUNCE_MS = 500;

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <TableRow>
      <TableCell sx={{ width: 140, color: "text.secondary", border: 0, py: 0.5 }}>
        {label}
      </TableCell>
      <TableCell sx={{ border: 0, py: 0.5, wordBreak: "break-all" }}>
        {value}
      </TableCell>
    </TableRow>
  );
}

function EventDetailDrawer({
  eventId,
  onClose,
}: {
  eventId: string | null;
  onClose: () => void;
}) {
  const [event, setEvent] = useState<TraceEvent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!eventId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvent(null);
    apiClient
      .get<TraceEvent>(`/trace/${encodeURIComponent(eventId)}`)
      .then((e) => {
        if (!cancelled) setEvent(e);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  return (
    <Drawer
      anchor="right"
      open={!!eventId}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 560 } } }}
    >
      <Box sx={{ p: 2 }}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ mb: 1 }}
        >
          <Typography variant="h6">Trace event</Typography>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>

        {loading && <CircularProgress sx={{ m: 2 }} />}
        {error && <Alert severity="error">{error}</Alert>}

        {event && (
          <>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Chip
                size="small"
                label={event.event_type}
                color={eventChipColor(event.event_type)}
              />
              <Typography variant="caption" color="text.secondary">
                {new Date(event.timestamp).toLocaleString()} (
                {relativeTime(event.timestamp)})
              </Typography>
            </Stack>

            <Table size="small" sx={{ mb: 1 }}>
              <TableBody>
                <MetaRow label="Event ID" value={event.id} />
                <MetaRow label="Session key" value={event.session_key} />
                <MetaRow label="Session ID" value={event.session_id} />
                <MetaRow label="Gateway" value={event.gateway_id} />
                <MetaRow label="Agent key" value={event.agent_key} />
                <MetaRow label="Parent event" value={event.parent_event_id} />
                {(event.fact_ids?.length ?? 0) > 0 && (
                  <MetaRow
                    label="Facts"
                    value={event.fact_ids!.join(", ")}
                  />
                )}
              </TableBody>
            </Table>

            <Divider sx={{ mb: 1 }} />
            <Typography variant="subtitle2" gutterBottom>
              Payload
            </Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                bgcolor: "action.hover",
                borderRadius: 1,
                overflow: "auto",
              }}
            >
              {JSON.stringify(event.payload ?? {}, null, 2)}
            </Box>

            <Typography variant="subtitle2" sx={{ mt: 2 }} gutterBottom>
              Full event
            </Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                bgcolor: "action.hover",
                borderRadius: 1,
                overflow: "auto",
              }}
            >
              {JSON.stringify(event, null, 2)}
            </Box>
          </>
        )}
      </Box>
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export const TraceListPage: React.FC = () => {
  const { data: perms, isLoading: permsLoading } = usePermissions<{
    authorityLevel?: number;
  }>();
  const authority = perms?.authorityLevel ?? 0;
  const canView = authority >= 70;

  // Filter + pagination state — seeded from the URL so a deep link, reload, or
  // Back navigation restores the exact view (consolidation-trace-9/-10). The
  // effect below mirrors state back to the URL (one-way, replace); only
  // non-default values are encoded to keep the query key stable.
  const [searchParams, setSearchParams] = useSearchParams();

  const [eventTypes, setEventTypes] = useState<EventTypeInfo[]>([]);
  const [selectedTypes, setSelectedTypes] = useState<string[]>(() => {
    const csv = searchParams.get("types");
    return csv ? csv.split(",").filter(Boolean) : [];
  });
  const [sessionKey, setSessionKey] = useState(
    () => searchParams.get("session_key") ?? "",
  );
  const [sessionId, setSessionId] = useState(
    () => searchParams.get("session_id") ?? "",
  );
  const [range, setRange] = useState<RangeKey>(() => {
    const r = searchParams.get("range");
    const valid = r === "all" || (TIME_RANGES as readonly string[]).includes(r ?? "");
    return valid ? (r as RangeKey) : "24h";
  });
  const [limit, setLimit] = useState(() => {
    const l = Number(searchParams.get("limit"));
    return LIMIT_OPTIONS.includes(l) ? l : 100;
  });
  const [offset, setOffset] = useState(() => {
    const o = Number(searchParams.get("offset"));
    return Number.isFinite(o) && o > 0 ? Math.floor(o) : 0;
  });
  const [refreshTick, setRefreshTick] = useState(0);

  // A filter edit invalidates the current page — always jump back to offset 0
  // so a narrower filter can't strand the operator on a now-empty later page.
  const resetOffset = () => setOffset(0);

  // Results state.
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const sessionIdTrimmed = sessionId.trim();
  const sessionIdInvalid =
    sessionIdTrimmed !== "" && !UUID_RE.test(sessionIdTrimmed);

  const typeDescriptions = useMemo(() => {
    const map: Record<string, string> = {};
    for (const et of eventTypes) map[et.type] = et.description;
    return map;
  }, [eventTypes]);

  // Mirror the filter/pagination state to the URL (only non-defaults, replace
  // to avoid spamming history) so it survives reload / Back / deep-link.
  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedTypes.length > 0) next.set("types", selectedTypes.join(","));
    if (sessionKey.trim()) next.set("session_key", sessionKey.trim());
    if (sessionId.trim()) next.set("session_id", sessionId.trim());
    if (range !== "24h") next.set("range", range);
    if (limit !== 100) next.set("limit", String(limit));
    if (offset > 0) next.set("offset", String(offset));
    setSearchParams(next, { replace: true });
  }, [selectedTypes, sessionKey, sessionId, range, limit, offset, setSearchParams]);

  // Load the event-type reference list once (feeds the multi-select).
  useEffect(() => {
    if (!canView) return;
    let cancelled = false;
    apiClient
      .get<EventTypeInfo[]>("/trace/event-types")
      .then((r) => {
        if (!cancelled && Array.isArray(r)) setEventTypes(r);
      })
      .catch(() => {
        /* filter options are a nicety — the query still works without them */
      });
    return () => {
      cancelled = true;
    };
  }, [canView]);

  // Debounced query on any filter change (PT-1: no auto-polling).
  useEffect(() => {
    if (!canView || sessionIdInvalid) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const body: Record<string, unknown> = { limit };
        if (offset > 0) body.offset = offset;
        if (selectedTypes.length > 0) body.event_types = selectedTypes;
        if (sessionKey.trim()) body.session_key = sessionKey.trim();
        if (sessionIdTrimmed) body.session_id = sessionIdTrimmed;
        if (range !== "all") {
          body.from_timestamp = new Date(
            Date.now() - RANGE_MS[range],
          ).toISOString();
        }
        const result = await request<TraceEvent[]>("/trace/query", {
          method: "POST",
          body,
          signal: controller.signal,
        });
        const rows = Array.isArray(result) ? result : [];
        rows.sort(
          (a, b) =>
            new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
        );
        setEvents(rows);
        setLoading(false);
      } catch (e) {
        if (controller.signal.aborted) return; // superseded by a newer query
        setError((e as Error).message);
        setLoading(false);
      }
    }, QUERY_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    canView,
    selectedTypes,
    sessionKey,
    sessionIdTrimmed,
    sessionIdInvalid,
    range,
    limit,
    offset,
    refreshTick,
  ]);

  if (permsLoading) return <CircularProgress sx={{ m: 4 }} />;

  if (!canView) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="h5" gutterBottom>
          Trace Explorer
        </Typography>
        <Alert severity="info">
          Viewing trace events requires authority level 70 (org admin). You
          have {authority}.
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Trace Explorer</Typography>
        <Button
          variant="outlined"
          size="small"
          startIcon={<RefreshIcon />}
          onClick={() => setRefreshTick((t) => t + 1)}
        >
          Refresh
        </Button>
      </Stack>

      {/* Filter bar */}
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={2}
          alignItems={{ md: "center" }}
          useFlexGap
          flexWrap="wrap"
        >
          <Autocomplete
            multiple
            size="small"
            limitTags={3}
            options={eventTypes.map((et) => et.type)}
            value={selectedTypes}
            onChange={(_, v) => {
              setSelectedTypes(v);
              resetOffset();
            }}
            sx={{ minWidth: 320, flex: 1 }}
            renderOption={(props, option) => {
              // React 18 forbids spreading a `key` in with the rest of the
              // props — MUI's `renderOption` props include one. Pull it out and
              // pass it explicitly to silence the console error
              // (consolidation-trace-11).
              const { key, ...optionProps } = props;
              return (
                <li key={key} {...optionProps}>
                  <Box>
                    <Typography variant="body2">{option}</Typography>
                    {typeDescriptions[option] && (
                      <Typography variant="caption" color="text.secondary">
                        {typeDescriptions[option]}
                      </Typography>
                    )}
                  </Box>
                </li>
              );
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Event types"
                placeholder={selectedTypes.length === 0 ? "All types" : ""}
              />
            )}
          />
          <TextField
            size="small"
            label="Session key"
            value={sessionKey}
            onChange={(e) => {
              setSessionKey(e.target.value);
              resetOffset();
            }}
            sx={{ minWidth: 200 }}
          />
          <TextField
            size="small"
            label="Session ID (UUID)"
            value={sessionId}
            onChange={(e) => {
              setSessionId(e.target.value);
              resetOffset();
            }}
            error={sessionIdInvalid}
            helperText={sessionIdInvalid ? "Not a valid UUID" : undefined}
            sx={{ minWidth: 280 }}
          />
          <ToggleButtonGroup
            size="small"
            exclusive
            value={range}
            onChange={(_, v) => {
              if (v) {
                setRange(v as RangeKey);
                resetOffset();
              }
            }}
          >
            {TIME_RANGES.map((r) => (
              <ToggleButton key={r} value={r}>
                {r}
              </ToggleButton>
            ))}
            <ToggleButton value="all">all</ToggleButton>
          </ToggleButtonGroup>
          <TextField
            select
            size="small"
            label="Limit"
            value={limit}
            onChange={(e) => {
              setLimit(Number(e.target.value));
              resetOffset();
            }}
            sx={{ minWidth: 100 }}
          >
            {LIMIT_OPTIONS.map((n) => (
              <MenuItem key={n} value={n}>
                {n}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </Paper>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading && events.length === 0 ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined" sx={{ opacity: loading ? 0.6 : 1 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 140 }}>Time</TableCell>
                <TableCell sx={{ width: 220 }}>Type</TableCell>
                <TableCell sx={{ width: 220 }}>Session</TableCell>
                <TableCell>Summary</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((e) => (
                <TableRow
                  key={e.id}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() => setDetailId(e.id)}
                >
                  <TableCell>
                    <Tooltip title={new Date(e.timestamp).toLocaleString()}>
                      <span>{relativeTime(e.timestamp)}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={e.event_type}
                      color={eventChipColor(e.event_type)}
                    />
                  </TableCell>
                  <TableCell sx={{ wordBreak: "break-all" }}>
                    <Typography variant="body2">
                      {/* `??` kept the empty-string session_key (rendering a
                          blank cell); `||` falls through to the id / em-dash
                          placeholder (consolidation-trace-8). */}
                      {e.session_key ||
                        (e.session_id ? e.session_id.slice(0, 8) : "—")}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {summarizeEvent(
                        e.event_type,
                        (e.payload ?? {}) as Record<string, unknown>,
                      )}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
              {events.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4}>
                    No trace events match the current filters.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* Offset/limit pagination — the backend /trace/query honours `offset`
          (consolidation-trace-10). "Next" is enabled only when the current page
          filled the limit (a full page implies there may be more). */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ mt: 1 }}
      >
        <Button
          size="small"
          disabled={offset === 0 || loading}
          onClick={() => setOffset((o) => Math.max(0, o - limit))}
        >
          Prev
        </Button>
        <Button
          size="small"
          disabled={events.length < limit || loading}
          onClick={() => setOffset((o) => o + limit)}
        >
          Next
        </Button>
        <Typography variant="caption" color="text.secondary">
          {events.length === 0
            ? "No events"
            : `Showing ${offset + 1}–${offset + events.length}`}
          {events.length >= limit ? ` (page size ${limit})` : ""}
        </Typography>
      </Stack>

      <EventDetailDrawer eventId={detailId} onClose={() => setDetailId(null)} />
    </Box>
  );
};

export default TraceListPage;
