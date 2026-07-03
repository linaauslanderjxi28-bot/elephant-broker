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
  CardContent,
  Chip,
  CircularProgress,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import {
  apiGet,
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
  components: Record<string, ComponentHealth>;
  recent_events: RecentEvent[];
}

function StatusCard(props: {
  label: string;
  value: number | string;
  tone?: "ok" | "warn" | "error";
}) {
  const color =
    props.tone === "error"
      ? "error.main"
      : props.tone === "warn"
        ? "warning.main"
        : "success.main";
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
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
    </Card>
  );
}

export const HomePage: React.FC = () => {
  const [range, setRange] = useState<TimeRange>("24h");
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
            />
          </Box>

          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ mt: 3, mb: 1 }}
          >
            <Typography variant="h6">System health</Typography>
            {data.system_health && (
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
            )}
          </Stack>
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
