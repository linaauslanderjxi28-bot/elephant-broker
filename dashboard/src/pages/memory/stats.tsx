// Memory Stats (`/memory/stats`) — memory health dashboard.
//
// Backend: `GET /dashboard/memory/stats?time_range=24h` -> MemoryStatsResponse.
// Neo4j current-state aggregates + activity rates (durable ClickHouse trace
// store when available, in-memory trace ledger otherwise) + a creation
// sparkline (MUI X Charts, AD-20). View-only.
//
// Implements plan Section 2 "Memory Stats" + SOW page 4.

import { useState, type FC } from "react";
import { useNavigate } from "react-router";
import { useApiUrl, useCustom } from "@refinedev/core";
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
  Link as MuiLink,
  Stack,
  Divider,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { BarChart } from "@mui/x-charts/BarChart";
import { SparkLineChart } from "@mui/x-charts/SparkLineChart";

import { humanizeEnum } from "../../lib/format";
import { actorDisplayName } from "../../lib/labels";
import {
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  SCOPE_LABELS,
  type MemoryClass,
  type MemoryStatsResponse,
  type MetricSnapshot,
  type MetricsSnapshotResponse,
  type Scope,
} from "./types";

/** Enum-key → label with a consistent Title-Case fallback (memory-stats-7). */
const classLabel = (k: string): string =>
  MEMORY_CLASS_LABELS[k.toLowerCase() as MemoryClass] ?? humanizeEnum(k);
const scopeDisplay = (k: string): string =>
  SCOPE_LABELS[k.toLowerCase() as Scope] ?? humanizeEnum(k);

const TIME_RANGES = ["1h", "6h", "24h", "7d"];

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <Card variant="outlined" sx={{ minWidth: 140 }}>
      <CardContent>
        <Typography variant="h5" sx={color ? { color } : undefined}>
          {value}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Runtime metrics section (GET /dashboard/metrics)
// ---------------------------------------------------------------------------
// The curated headline counters/gauges surfaced by the backend allow-list,
// grouped by domain. Only metrics actually present for the caller's gateway are
// rendered (a metric with no series is omitted by the backend), so an empty
// group is skipped rather than shown as a row of zeros. Names are the EXPOSED
// Prometheus series names (matching MetricSnapshot.name).
const METRIC_COUNTER_GROUPS: {
  group: string;
  metrics: { name: string; label: string }[];
}[] = [
  {
    group: "Memory",
    metrics: [
      { name: "eb_facts_stored_total", label: "Facts stored" },
      { name: "eb_facts_superseded_total", label: "Facts superseded" },
      { name: "eb_dedup_checks_total", label: "Dedup checks" },
    ],
  },
  {
    group: "Retrieval",
    metrics: [{ name: "eb_retrieval_total", label: "Retrievals" }],
  },
  {
    group: "Guards",
    metrics: [
      { name: "eb_guard_checks_total", label: "Guard checks" },
      { name: "eb_guard_near_misses_total", label: "Near misses" },
      { name: "eb_guard_llm_escalations_total", label: "LLM escalations" },
    ],
  },
  {
    group: "Working set",
    metrics: [
      { name: "eb_working_set_builds_total", label: "Working-set builds" },
      { name: "eb_rerank_calls_total", label: "Rerank calls" },
      { name: "eb_rerank_fallbacks_total", label: "Rerank fallbacks" },
    ],
  },
  {
    group: "LLM",
    metrics: [
      { name: "eb_llm_calls_total", label: "LLM calls" },
      { name: "eb_llm_tokens_used_total", label: "Tokens used" },
    ],
  },
  {
    group: "Consolidation",
    metrics: [
      { name: "eb_compaction_triggered_total", label: "Compactions" },
      { name: "eb_consolidation_runs_total", label: "Consolidation runs" },
    ],
  },
];

// Histogram families surfaced as an avg-latency row (avg = _sum / _count, in ms).
const METRIC_LATENCY: { name: string; label: string }[] = [
  { name: "eb_retrieval_duration_seconds", label: "Retrieval" },
  { name: "eb_guard_check_duration_seconds", label: "Guard check" },
  { name: "eb_working_set_build_duration_seconds", label: "Working set" },
  { name: "eb_rerank_duration_seconds", label: "Rerank" },
  { name: "eb_llm_duration_seconds", label: "LLM" },
  { name: "eb_lifecycle_duration_seconds", label: "Lifecycle" },
];

const metricByName = (metrics: MetricSnapshot[], name: string): MetricSnapshot | undefined =>
  metrics.find((m) => m.name === name);

/** Sum every series value of a counter/gauge family (across its label sets). */
const seriesTotal = (m?: MetricSnapshot): number =>
  (m?.series ?? []).reduce((acc, s) => acc + (s.value ?? 0), 0);

/** Mean observation of a histogram family in milliseconds, or null if no counts. */
const histogramAvgMs = (m?: MetricSnapshot): number | null => {
  let sum = 0;
  let count = 0;
  for (const s of m?.series ?? []) {
    sum += s.sum ?? 0;
    count += s.count ?? 0;
  }
  return count > 0 ? (sum / count) * 1000 : null;
};

/**
 * Runtime Metrics — the gateway-scoped Prometheus registry projection served by
 * `GET /dashboard/metrics`. These are process counters/gauges/histograms that are
 * CUMULATIVE SINCE PROCESS START and reset on restart, so the section is labelled
 * distinctly from the Neo4j current-state totals above it. Degrades to a subtle
 * note when `prometheus_client` is absent ({ available: false }).
 */
function RuntimeMetricsSection({ apiUrl }: { apiUrl: string }) {
  const { data, isLoading } = useCustom<MetricsSnapshotResponse>({
    url: `${apiUrl}/dashboard/metrics`,
    method: "get",
  });

  const snap = data?.data;

  if (isLoading || !snap) {
    return null;
  }

  // Degraded path: prometheus_client absent -> no registry to project.
  if (snap.available === false) {
    return (
      <Box sx={{ mt: 1 }}>
        <Divider sx={{ mb: 2 }} />
        <Typography variant="caption" color="text.secondary">
          Prometheus metrics unavailable{snap.note ? ` — ${snap.note}` : "."}
        </Typography>
      </Box>
    );
  }

  const metrics = snap.metrics ?? [];

  // Gauges (current instantaneous values, not cumulative counters).
  const sessions = metricByName(metrics, "eb_session_active");
  const bootstrap = metricByName(metrics, "eb_bootstrap_mode_active");
  const health = metricByName(metrics, "eb_backend_health");

  const latencies = METRIC_LATENCY.map((l) => ({
    ...l,
    avg: histogramAvgMs(metricByName(metrics, l.name)),
  })).filter((l) => l.avg !== null);

  return (
    <Box>
      <Divider sx={{ mb: 2 }} />
      <Typography variant="subtitle1" gutterBottom>
        Runtime Metrics
      </Typography>
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 2 }}>
        Cumulative since process start (resets on restart) — distinct from the
        graph-store current-state totals above.
        {snap.generated_at
          ? ` Snapshot at ${new Date(snap.generated_at).toLocaleString()}.`
          : ""}
      </Typography>

      <Stack spacing={3}>
        {/* Counter groups — one row of tiles per domain, empty groups skipped. */}
        {METRIC_COUNTER_GROUPS.map((g) => {
          const present = g.metrics
            .map((m) => ({ label: m.label, metric: metricByName(metrics, m.name) }))
            .filter((m) => m.metric !== undefined);
          if (present.length === 0) return null;
          return (
            <Box key={g.group}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                {g.group}
              </Typography>
              <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                {present.map((m) => (
                  <Tooltip key={m.label} title={m.metric?.help ?? ""}>
                    <span>
                      <StatCard label={m.label} value={seriesTotal(m.metric).toLocaleString()} />
                    </span>
                  </Tooltip>
                ))}
              </Stack>
            </Box>
          );
        })}

        {/* Average latencies derived from histogram _sum / _count. */}
        {latencies.length > 0 && (
          <Box>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Average latency
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              {latencies.map((l) => (
                <StatCard key={l.name} label={l.label} value={`${(l.avg as number).toFixed(1)} ms`} />
              ))}
            </Stack>
          </Box>
        )}

        {/* Gauges — instantaneous health/liveness readings. */}
        {(sessions || bootstrap || health) && (
          <Box>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Live gauges
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap alignItems="center">
              {sessions && (
                <StatCard label="Active sessions" value={seriesTotal(sessions).toLocaleString()} />
              )}
              {bootstrap && (
                <StatCard
                  label="Bootstrap mode"
                  value={seriesTotal(bootstrap) > 0 ? "On" : "Off"}
                />
              )}
              {health && health.series.length > 0 && (
                <Card variant="outlined">
                  <CardContent>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {health.series.map((s, i) => {
                        const up = (s.value ?? 0) > 0;
                        return (
                          <Chip
                            key={`${s.labels.component ?? i}`}
                            size="small"
                            label={humanizeEnum(s.labels.component ?? "component")}
                            color={up ? "success" : "error"}
                            variant={up ? "filled" : "outlined"}
                          />
                        );
                      })}
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      Backend health
                    </Typography>
                  </CardContent>
                </Card>
              )}
            </Stack>
          </Box>
        )}
      </Stack>
    </Box>
  );
}

export const MemoryStats: FC = () => {
  const navigate = useNavigate();
  const apiUrl = useApiUrl();
  const [timeRange, setTimeRange] = useState("24h");

  const { data, isLoading, isError } = useCustom<MemoryStatsResponse>({
    url: `${apiUrl}/dashboard/memory/stats`,
    method: "get",
    config: { query: { time_range: timeRange } },
  });

  const stats = data?.data;

  // memory-stats-2 (FE mitigation): drop placeholder rows for an empty-string
  // actor so the table never shows a blank first row. The real fix (not emitting
  // the empty actor) is backend-side.
  const topActors = (stats?.top_actors ?? []).filter(
    (a) => (a.actor_label || a.actor_id || "").trim() !== "",
  );

  const classColor = (cls: string): string | undefined => MEMORY_CLASS_HEX[cls as MemoryClass];

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5">Memory Stats</Typography>
        <Stack direction="row" spacing={1}>
          {TIME_RANGES.map((tr) => (
            <Chip
              key={tr}
              label={tr}
              color={tr === timeRange ? "primary" : "default"}
              onClick={() => setTimeRange(tr)}
            />
          ))}
        </Stack>
      </Stack>

      {isLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
          <CircularProgress />
        </Box>
      )}

      {isError && <Alert severity="error">Could not load memory stats.</Alert>}

      {stats && !isLoading && (
        <Stack spacing={3}>
          {/* Current state — totals by type */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Totals by type ({stats.total_facts.toLocaleString()} facts)
            </Typography>
            {Object.keys(stats.by_class).length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No facts recorded yet.
              </Typography>
            ) : (
              <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                {Object.entries(stats.by_class).map(([k, v]) => (
                  <StatCard
                    key={k}
                    label={classLabel(k)}
                    value={v.toLocaleString()}
                    color={classColor(k.toLowerCase())}
                  />
                ))}
              </Stack>
            )}
          </Box>

          {/* Totals by scope */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Totals by scope
            </Typography>
            {Object.keys(stats.by_scope).length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No scoped facts yet.
              </Typography>
            ) : (
              <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                {Object.entries(stats.by_scope).map(([k, v]) => (
                  <StatCard key={k} label={scopeDisplay(k)} value={v.toLocaleString()} />
                ))}
              </Stack>
            )}
          </Box>

          {/* Quality indicators */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Quality
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              <StatCard
                label="Avg confidence"
                value={`${Math.round(stats.avg_confidence * 100)}%`}
              />
              <StatCard
                label="Avg usage (recalls/fact)"
                value={stats.avg_use_count.toFixed(1)}
              />
              <StatCard
                label="Success rate"
                // memory-stats-6: a flat "0%" reads as "everything failed" when the
                // real story is "nothing has been recalled yet". Show "—" until
                // there is usage to compute a rate from.
                value={
                  stats.avg_use_count > 0
                    ? `${Math.round(stats.avg_success_rate * 100)}%`
                    : "—"
                }
              />
            </Stack>
          </Box>

          {/* Activity (time-range dependent) */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Activity ({timeRange})
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
              <StatCard label="Facts extracted" value={stats.extractions_in_period.toLocaleString()} />
              <StatCard label="Duplicate rate" value={`${Math.round(stats.dedup_rate * 100)}%`} />
              <StatCard
                label="Supersession rate"
                value={`${Math.round(stats.supersession_rate * 100)}%`}
              />
            </Stack>

            {stats.creation_over_time.length > 0 ? (
              <Card variant="outlined">
                <CardContent>
                  <Typography variant="caption" color="text.secondary">
                    Creation over time
                  </Typography>
                  <Box sx={{ mt: 1 }}>
                    <SparkLineChart
                      data={stats.creation_over_time.map((b) => b.count)}
                      height={80}
                      area
                      showTooltip
                      showHighlight
                      xAxis={{
                        scaleType: "point",
                        data: stats.creation_over_time.map((b) =>
                          new Date(b.timestamp).toLocaleString(),
                        ),
                      }}
                    />
                  </Box>
                </CardContent>
              </Card>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No creation activity in this period.
              </Typography>
            )}
          </Box>

          {/* By-class distribution bar chart */}
          {Object.keys(stats.by_class).length > 0 && (
            <Card variant="outlined">
              <CardContent>
                <Typography variant="caption" color="text.secondary">
                  Fact distribution by type
                </Typography>
                <BarChart
                  height={260}
                  xAxis={[
                    {
                      scaleType: "band",
                      data: Object.keys(stats.by_class).map((k) => classLabel(k)),
                    },
                  ]}
                  series={[{ data: Object.values(stats.by_class), label: "Facts" }]}
                />
              </CardContent>
            </Card>
          )}

          {/* Advanced: top actors */}
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Top actors by fact count</Typography>
            </AccordionSummary>
            <AccordionDetails>
              {topActors.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No actor data.
                </Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Actor</TableCell>
                      <TableCell align="right">Facts</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {topActors.map((a) => (
                      <TableRow key={a.actor_id} hover>
                        <TableCell>
                          <MuiLink
                            component="button"
                            onClick={() => navigate(`/actors/${a.actor_id}`)}
                          >
                            {actorDisplayName(a.actor_label || a.actor_id)}
                          </MuiLink>
                        </TableCell>
                        <TableCell align="right">{a.fact_count.toLocaleString()}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </AccordionDetails>
          </Accordion>

          {/* Advanced: raw numbers */}
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Raw numbers</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box component="pre" sx={{ overflow: "auto", fontSize: 12 }}>
                {JSON.stringify(stats, null, 2)}
              </Box>
            </AccordionDetails>
          </Accordion>
        </Stack>
      )}

      {/* Runtime metrics — cumulative process counters/gauges from the Prometheus
          registry, gateway-scoped. Rendered independently of the memory-stats
          load (own fetch + loading/degraded handling). */}
      <Box sx={{ mt: 3 }}>
        <RuntimeMetricsSection apiUrl={apiUrl} />
      </Box>

      <Box sx={{ mt: 2 }}>
        <Typography variant="caption" color="text.secondary" component="div">
          Current-state figures come from the graph store; activity rates and the sparkline are
          served over the selected window from {stats?.activity_source_label ?? "the in-memory trace ledger"}.
          For deep analytics use Grafana.
        </Typography>
        {stats?.note && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5 }}>
            {stats.note}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

export default MemoryStats;
