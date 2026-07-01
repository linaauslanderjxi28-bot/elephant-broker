// Memory Stats (`/memory/stats`) — memory health dashboard.
//
// Backend: `GET /dashboard/memory/stats?time_range=24h` -> MemoryStatsResponse.
// Neo4j current-state aggregates + ClickHouse activity rates + a creation
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { BarChart } from "@mui/x-charts/BarChart";
import { SparkLineChart } from "@mui/x-charts/SparkLineChart";

import {
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  SCOPE_LABELS,
  type MemoryClass,
  type MemoryStatsResponse,
  type Scope,
} from "./types";

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
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              {Object.entries(stats.by_class).map(([k, v]) => (
                <StatCard
                  key={k}
                  label={MEMORY_CLASS_LABELS[k.toLowerCase() as MemoryClass] ?? k}
                  value={v.toLocaleString()}
                  color={classColor(k.toLowerCase())}
                />
              ))}
            </Stack>
          </Box>

          {/* Totals by scope */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Totals by scope
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              {Object.entries(stats.by_scope).map(([k, v]) => (
                <StatCard
                  key={k}
                  label={SCOPE_LABELS[k.toLowerCase() as Scope] ?? k}
                  value={v.toLocaleString()}
                />
              ))}
            </Stack>
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
                value={`${Math.round(stats.avg_success_rate * 100)}%`}
              />
            </Stack>
          </Box>

          {/* Activity (time-range dependent) */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              Activity ({timeRange})
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
              <StatCard label="facts extracted" value={stats.extractions_in_period.toLocaleString()} />
              <StatCard label="duplicate rate" value={`${Math.round(stats.dedup_rate * 100)}%`} />
              <StatCard
                label="supersession rate"
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
                      data: Object.keys(stats.by_class).map(
                        (k) => MEMORY_CLASS_LABELS[k.toLowerCase() as MemoryClass] ?? k,
                      ),
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
              {stats.top_actors.length === 0 ? (
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
                    {stats.top_actors.map((a) => (
                      <TableRow key={a.actor_id} hover>
                        <TableCell>
                          <MuiLink
                            component="button"
                            onClick={() => navigate(`/actors/${a.actor_id}`)}
                          >
                            {a.actor_label || a.actor_id}
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

      <Box sx={{ mt: 2 }}>
        <Typography variant="caption" color="text.secondary">
          Current-state figures come from Neo4j; activity rates and the sparkline come from
          ClickHouse over the selected window. For deep analytics use Grafana.
        </Typography>
      </Box>
    </Box>
  );
};

export default MemoryStats;
