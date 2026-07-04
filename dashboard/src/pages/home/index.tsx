// Home / Overview page.
//
// Landing page: status cards, system-component health, and a recent-activity
// feed. Sources GET /dashboard/overview?time_range=... All "in period" counts
// follow the selected time range.

import React, { useCallback, useEffect, useState } from "react";
import { useNavigation, usePermissions } from "@refinedev/core";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Link,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import CloseIcon from "@mui/icons-material/Close";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  apiGet,
  apiSend,
  COMPONENT_LABELS,
  eventChipColor,
  relativeTime,
  summarizeEvent,
  TIME_RANGES,
  type TimeRange,
} from "../home/dashboardApi";
import { humanizeEnum } from "../../lib/format";

// Dashboard read routes require authority >= 70 (backend READ dependency).
const MIN_READ_AUTHORITY = 70;

type HealthTone = "success" | "warning" | "error" | "default";
function systemHealthColor(health: string | undefined): HealthTone {
  switch ((health || "").toLowerCase()) {
    case "healthy":
      return "success";
    case "degraded":
      return "warning";
    case "unhealthy":
      return "error";
    default:
      return "default";
  }
}

/**
 * Friendly access-denied panel for authenticated-but-under-authority operators
 * (auth-3). Fresh dashboard users have authority 0; every read 403s. Rather
 * than a wall of raw "403 Forbidden" errors, explain the situation.
 */
function NotAuthorized({ authority }: { authority: number }) {
  return (
    <Box sx={{ p: 2, display: "flex", justifyContent: "center" }}>
      <Card variant="outlined" sx={{ maxWidth: 520, width: "100%", mt: 6 }}>
        <CardContent sx={{ textAlign: "center", py: 4 }}>
          <LockOutlinedIcon sx={{ fontSize: 48, color: "text.disabled" }} />
          <Typography variant="h6" sx={{ mt: 1 }}>
            Access pending
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Your account is signed in, but it doesn&rsquo;t yet have permission
            to view dashboard data. An administrator needs to raise your
            authority level to at least {MIN_READ_AUTHORITY}.
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ mt: 2, display: "block" }}>
            Current authority level: {authority}
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
}

interface ComponentHealth {
  status: string;
  latency_ms: number | null;
}
interface RecentEvent {
  timestamp: string;
  summary: string;
  event_type: string;
  session_key: string | null;
}
/** Mirror of schemas/dashboard.py ErrorSummary — one degraded_operation event. */
interface ErrorSummary {
  component: string;
  error: string;
  timestamp: string;
  session_key: string | null;
}
interface Overview {
  time_range: string;
  total_facts: number;
  facts_in_period: number;
  facts_by_class: Record<string, number>;
  facts_by_scope: Record<string, number>;
  active_sessions: number;
  total_actors: number;
  total_organizations: number;
  total_goals_active: number;
  guard_triggers_in_period: number;
  guard_near_misses_in_period: number;
  errors_in_period: number;
  system_health: string;
  // Human-readable reasons behind system_health; [] when healthy.
  health_reasons: string[];
  // Up to 20 most-recent degraded_operation events in the window, newest first.
  recent_errors: ErrorSummary[];
  components: Record<string, ComponentHealth>;
  recent_events: RecentEvent[];
}

function StatusCard(props: {
  label: string;
  value: number | string;
  tone?: "ok" | "warn" | "error";
  // When provided, the whole card becomes an actionable button (cursor + hover
  // affordance). Omitted => the card stays inert, exactly as before.
  onClick?: () => void;
}) {
  const color =
    props.tone === "error"
      ? "error.main"
      : props.tone === "warn"
        ? "warning.main"
        : "success.main";
  const inner = (
    <CardContent>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box
          sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: color }}
        />
        <Typography variant="body2" color="text.secondary">
          {props.label}
        </Typography>
      </Stack>
      <Typography variant="h4" sx={{ mt: 1 }}>
        {props.value}
      </Typography>
    </CardContent>
  );
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      {props.onClick ? (
        <CardActionArea onClick={props.onClick} sx={{ height: "100%" }}>
          {inner}
        </CardActionArea>
      ) : (
        inner
      )}
    </Card>
  );
}

/**
 * Drill-down dialog for the "Errors (range)" tile. Renders the recent
 * degraded_operation events already on the overview payload (component chip +
 * raw message + relative time + session). If the list is empty (e.g. a slim
 * payload), it lazily falls back to POST /trace/query — gateway-scoped
 * server-side, behind the same READ auth. A "View all in Trace Explorer" link
 * jumps to /trace pre-filtered to degraded_operation events.
 */
function ErrorsDialog(props: {
  open: boolean;
  onClose: () => void;
  errors: ErrorSummary[];
  timeRange: string;
  onViewAll: () => void;
}) {
  const { open, onClose, errors, timeRange, onViewAll } = props;
  const [fetched, setFetched] = useState<ErrorSummary[] | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const needsFallback = open && errors.length === 0;

  useEffect(() => {
    if (!needsFallback) return;
    let cancelled = false;
    setFetching(true);
    setFetchError(null);
    // The overview window is a simple lookback; mirror the trace page and pass a
    // generous limit — the server caps and gateway-scopes the result.
    const since = new Date(Date.now() - 7 * 86_400_000).toISOString();
    apiSend<any[]>("POST", "/trace/query", {
      event_types: ["degraded_operation"],
      from_timestamp: since,
      limit: 20,
    })
      .then((rows) => {
        if (cancelled) return;
        const mapped: ErrorSummary[] = (Array.isArray(rows) ? rows : []).map(
          (e) => ({
            component: String((e?.payload?.component ?? "") || ""),
            error: String((e?.payload?.error ?? "") || ""),
            timestamp: e?.timestamp,
            session_key: e?.session_key ?? null,
          }),
        );
        mapped.sort(
          (a, b) =>
            new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
        );
        setFetched(mapped);
      })
      .catch((e) => {
        if (!cancelled) setFetchError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [needsFallback]);

  const rows = errors.length > 0 ? errors : (fetched ?? []);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pr: 6 }}>
        Errors (last {timeRange})
        <IconButton
          aria-label="Close"
          onClick={onClose}
          size="small"
          sx={{ position: "absolute", right: 8, top: 8 }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {fetchError && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {fetchError}
          </Alert>
        )}
        {fetching && rows.length === 0 ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
            <CircularProgress size={24} />
          </Box>
        ) : rows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No error details available.
          </Typography>
        ) : (
          <List dense disablePadding>
            {rows.map((ev, i) => (
              <ListItem
                key={i}
                alignItems="flex-start"
                divider={i < rows.length - 1}
                sx={{ px: 0 }}
              >
                <ListItemText
                  primary={
                    <Stack
                      direction="row"
                      spacing={1}
                      alignItems="center"
                      flexWrap="wrap"
                    >
                      {ev.component && (
                        <Chip size="small" color="error" label={ev.component} />
                      )}
                      <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
                        {ev.error || "Unknown error"}
                      </Typography>
                    </Stack>
                  }
                  secondary={
                    <>
                      {relativeTime(ev.timestamp)}
                      {ev.session_key ? ` · ${ev.session_key}` : ""}
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
        <Divider sx={{ my: 1.5 }} />
        <Link
          component="button"
          type="button"
          onClick={onViewAll}
          sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
        >
          View all in Trace Explorer
          <OpenInNewIcon sx={{ fontSize: 16 }} />
        </Link>
      </DialogContent>
    </Dialog>
  );
}

export const HomePage: React.FC = () => {
  const [range, setRange] = useState<TimeRange>("24h");
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorsOpen, setErrorsOpen] = useState(false);
  const { push } = useNavigation();
  const { data: perms, isLoading: permsLoading } = usePermissions<{
    authorityLevel?: number;
  }>();
  const authority = perms?.authorityLevel ?? 0;
  const authorized = authority >= MIN_READ_AUTHORITY;

  const load = useCallback(async () => {
    // Reads require authority >= 70; skip the request (it would 403) and let the
    // friendly access-denied panel handle under-authority operators.
    if (!authorized) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<Overview>("/dashboard/overview", {
        time_range: range,
      });
      setData(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [range, authorized]);

  useEffect(() => {
    if (permsLoading) return;
    void load();
  }, [load, permsLoading]);

  if (permsLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (!authorized) {
    return <NotAuthorized authority={authority} />;
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Overview</Typography>
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
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {loading && !data ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
          <CircularProgress />
        </Box>
      ) : data ? (
        <>
          <Box
            sx={{
              display: "grid",
              gap: 2,
              gridTemplateColumns: {
                xs: "repeat(2, 1fr)",
                md: "repeat(5, 1fr)",
              },
            }}
          >
            <StatusCard label="Total facts" value={data.total_facts} />
            <StatusCard
              label={`Facts (${range})`}
              value={data.facts_in_period}
            />
            <StatusCard label="Active sessions" value={data.active_sessions} />
            <StatusCard
              label={`Guard triggers (${range})`}
              value={data.guard_triggers_in_period}
              tone={data.guard_triggers_in_period > 0 ? "warn" : "ok"}
            />
            <StatusCard
              label={`Errors (${range})`}
              value={data.errors_in_period}
              tone={data.errors_in_period > 0 ? "error" : "ok"}
              // Actionable only when there's something to drill into; the tile
              // stays inert (no cursor/hover) at zero.
              onClick={
                data.errors_in_period > 0
                  ? () => setErrorsOpen(true)
                  : undefined
              }
            />
          </Box>

          <ErrorsDialog
            open={errorsOpen}
            onClose={() => setErrorsOpen(false)}
            errors={data.recent_errors || []}
            timeRange={data.time_range || range}
            onViewAll={() => {
              setErrorsOpen(false);
              // Match trace/list.tsx's URL filter shape: `types` is a CSV of
              // event_type values it splits on load.
              push("/trace?types=degraded_operation");
            }}
          />

          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ mt: 3, mb: 1 }}
          >
            <Typography variant="h6">System health</Typography>
            {data.system_health && (
              <Tooltip
                title={
                  (data.health_reasons || []).length > 0
                    ? (data.health_reasons || []).join("; ")
                    : ""
                }
                arrow
              >
                <Chip
                  size="small"
                  label={humanizeEnum(data.system_health)}
                  color={systemHealthColor(data.system_health)}
                  variant={
                    systemHealthColor(data.system_health) === "success"
                      ? "outlined"
                      : "filled"
                  }
                />
              </Tooltip>
            )}
          </Stack>
          {/* Spell out WHY the status isn't healthy — a caption list built from
              the same signals the backend derived system_health from. Nothing
              shown when healthy (health_reasons is empty). */}
          {(data.health_reasons || []).length > 0 && (
            <Stack spacing={0.25} sx={{ mb: 1 }}>
              {(data.health_reasons || []).map((reason, i) => (
                <Typography
                  key={i}
                  variant="caption"
                  color="text.secondary"
                >
                  {/* Status prefix on the first line only, so a multi-reason
                      list reads as "Degraded — reason / reason" not a repeated
                      "Degraded —" wall. */}
                  {i === 0 ? `${humanizeEnum(data.system_health)} — ` : ""}
                  {reason}
                </Typography>
              ))}
            </Stack>
          )}
          <Box
            sx={{
              display: "grid",
              gap: 2,
              gridTemplateColumns: {
                xs: "repeat(2, 1fr)",
                sm: "repeat(3, 1fr)",
                md: "repeat(5, 1fr)",
              },
            }}
          >
            {Object.entries(data.components || {}).map(([key, c]) => {
              const ok = c.status === "ok";
              return (
                <Card variant="outlined" key={key}>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: "50%",
                          bgcolor: ok ? "success.main" : "error.main",
                        }}
                      />
                      <Typography variant="body2">
                        {COMPONENT_LABELS[key] || key}
                      </Typography>
                    </Stack>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ mt: 0.5, display: "block" }}
                    >
                      {c.status}
                      {c.latency_ms != null
                        ? ` · ${c.latency_ms.toFixed(0)}ms`
                        : ""}
                    </Typography>
                  </CardContent>
                </Card>
              );
            })}
          </Box>

          <Typography variant="h6" sx={{ mt: 3, mb: 1 }}>
            Recent activity
          </Typography>
          <Card variant="outlined">
            <List dense>
              {(data.recent_events || []).length === 0 && (
                <ListItemText
                  sx={{ px: 2, py: 1 }}
                  primary="No recent events."
                />
              )}
              {(data.recent_events || []).map((ev, i) => {
                const chip = (
                  <Chip
                    size="small"
                    label={humanizeEnum(ev.event_type)}
                    color={eventChipColor(ev.event_type)}
                    sx={{ mr: 1.5, minWidth: 120 }}
                  />
                );
                const text = (
                  <ListItemText
                    primary={
                      ev.summary || summarizeEvent(ev.event_type, ev as any)
                    }
                    secondary={relativeTime(ev.timestamp)}
                  />
                );
                // Only rows with a session to open are actually navigable —
                // non-navigable rows render as plain list items so they don't
                // look clickable (overview-4).
                return ev.session_key ? (
                  <ListItemButton
                    key={i}
                    onClick={() =>
                      push(
                        `/sessions/${encodeURIComponent(ev.session_key as string)}`,
                      )
                    }
                  >
                    {chip}
                    {text}
                  </ListItemButton>
                ) : (
                  <ListItem key={i}>
                    {chip}
                    {text}
                  </ListItem>
                );
              })}
            </List>
          </Card>

          <Accordion sx={{ mt: 2 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Advanced — fact breakdown</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box
                sx={{
                  display: "grid",
                  gap: 3,
                  gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                }}
              >
                <Box>
                  <Typography variant="subtitle2">By class</Typography>
                  {Object.entries(data.facts_by_class || {}).map(([k, v]) => (
                    <Stack
                      key={k}
                      direction="row"
                      justifyContent="space-between"
                    >
                      <Typography variant="body2">
                        {humanizeEnum(k) || "—"}
                      </Typography>
                      <Typography variant="body2">{v}</Typography>
                    </Stack>
                  ))}
                </Box>
                <Box>
                  <Typography variant="subtitle2">By scope</Typography>
                  {Object.entries(data.facts_by_scope || {}).map(([k, v]) => (
                    <Stack
                      key={k}
                      direction="row"
                      justifyContent="space-between"
                    >
                      <Typography variant="body2">
                        {humanizeEnum(k) || "—"}
                      </Typography>
                      <Typography variant="body2">{v}</Typography>
                    </Stack>
                  ))}
                </Box>
                <Box sx={{ gridColumn: { md: "1 / -1" } }}>
                  <Stack direction="row" spacing={3} flexWrap="wrap">
                    <Typography variant="body2">
                      Actors: {data.total_actors}
                    </Typography>
                    <Typography variant="body2">
                      Organizations: {data.total_organizations}
                    </Typography>
                    <Typography variant="body2">
                      Active goals: {data.total_goals_active}
                    </Typography>
                    <Typography variant="body2">
                      Near-misses: {data.guard_near_misses_in_period}
                    </Typography>
                  </Stack>
                </Box>
              </Box>
            </AccordionDetails>
          </Accordion>
        </>
      ) : null}
    </Box>
  );
};

export default HomePage;
