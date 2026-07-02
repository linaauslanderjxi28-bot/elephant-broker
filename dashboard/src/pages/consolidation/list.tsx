// Consolidation ("sleep") pipeline page.
//
// Sections:
//  - Status header: polls GET /consolidation/status (running / idle, current
//    stage, last run time) + "Run consolidation" trigger (authority >= 90).
//  - Reports tab: GET /consolidation/reports (newest first) with a detail
//    drawer (GET /consolidation/reports/{id}) showing summary + per-stage
//    results.
//  - Procedure suggestions tab: GET /consolidation/suggestions queue with
//    Approve/Reject (authority >= 70) via PATCH /consolidation/suggestions/{id}.
//    The backend PATCH schema only accepts `approval_status` — there is no
//    reason/notes field.

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Drawer,
  IconButton,
  LinearProgress,
  MenuItem,
  Paper,
  Snackbar,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { apiGet, apiSend, relativeTime, useAuthority } from "../home/dashboardApi";

// --- Local types (mirror elephantbroker/schemas/consolidation.py) ------------

interface StageResult {
  stage: number;
  name: string;
  items_processed: number;
  items_affected: number;
  llm_calls_made: number;
  duration_ms: number;
  details: Record<string, any>;
}

interface ConsolidationSummary {
  duplicates_merged: number;
  facts_strengthened: number;
  facts_decayed: number;
  facts_archived: number;
  autorecall_blacklisted: number;
  episodic_promoted: number;
  procedures_suggested: number;
  verification_gaps_found: number;
  weight_adjustments: Record<string, number>;
  global_promotion_candidates: number;
}

interface ConsolidationReport {
  id: string;
  org_id: string;
  gateway_id: string;
  profile_id?: string | null;
  started_at: string;
  completed_at?: string | null;
  status: string; // running | completed | failed | partial
  stage_results: StageResult[];
  summary?: ConsolidationSummary | null;
  error?: string | null;
}

interface ConsolidationStatus {
  running: boolean;
  started_at?: string;
  current_stage?: number;
  last_run_at?: string;
}

/** Row shape returned by GET /consolidation/suggestions (SQLite columns). */
interface ProcedureSuggestion {
  id: string;
  report_id?: string | null;
  gateway_id: string;
  pattern_description: string;
  tool_sequence_json: string;
  sessions_observed: number;
  draft_procedure_json?: string | null;
  confidence: number;
  approval_status: string; // pending | approved | rejected
  created_at: string;
}

// --- Constants / helpers ------------------------------------------------------

const PROFILES = ["coding", "research", "managerial", "worker", "personal_assistant"];

const STAGE_NAMES: Record<number, string> = {
  1: "Cluster near-duplicates",
  2: "Canonicalize",
  3: "Strengthen useful facts",
  4: "Decay unused facts",
  5: "Prune bad autorecall",
  6: "Promote episodic to semantic",
  7: "Refine procedures",
  8: "Identify verification gaps",
  9: "Recompute salience priors",
};
const STAGE_COUNT = 9;

const SUMMARY_LABELS: Array<[keyof ConsolidationSummary, string]> = [
  ["duplicates_merged", "Duplicates merged"],
  ["facts_strengthened", "Strengthened"],
  ["facts_decayed", "Decayed"],
  ["facts_archived", "Archived"],
  ["autorecall_blacklisted", "Autorecall blacklisted"],
  ["episodic_promoted", "Promoted to semantic"],
  ["procedures_suggested", "Procedures suggested"],
  ["verification_gaps_found", "Verification gaps"],
  ["global_promotion_candidates", "Global promotion candidates"],
];

type MuiColor =
  | "default"
  | "primary"
  | "secondary"
  | "error"
  | "info"
  | "success"
  | "warning";

function reportStatusColor(status: string | undefined): MuiColor {
  switch ((status || "").toLowerCase()) {
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "partial":
      return "warning";
    case "running":
      return "info";
    default:
      return "default";
  }
}

function suggestionStatusColor(status: string | undefined): MuiColor {
  switch ((status || "").toLowerCase()) {
    case "approved":
      return "success";
    case "rejected":
      return "error";
    case "pending":
      return "warning";
    default:
      return "default";
  }
}

function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}

function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function parseJsonSafe(raw: string | null | undefined): any {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Turn apiSend's "409 Conflict"-style errors into operator-friendly text. */
function friendlyRunError(message: string): string {
  if (message.startsWith("409")) {
    return "A consolidation run is already in progress for this gateway.";
  }
  if (message.startsWith("501")) {
    return "The consolidation engine is not available on this runtime.";
  }
  return message;
}

// --- Run confirmation dialog ---------------------------------------------------

function RunDialog(props: {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onConfirm: (profileId: string | null) => void;
  error: string | null;
}) {
  const [profile, setProfile] = useState("");

  useEffect(() => {
    if (props.open) setProfile("");
  }, [props.open]);

  return (
    <Dialog open={props.open} onClose={props.busy ? undefined : props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Run consolidation?</DialogTitle>
      <DialogContent>
        {props.error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {props.error}
          </Alert>
        )}
        <DialogContentText sx={{ mb: 2 }}>
          This triggers the 9-stage "sleep" pipeline for this gateway: duplicate
          clustering, canonicalization, strengthen/decay, autorecall pruning,
          episodic promotion, procedure refinement, verification-gap detection,
          and salience recomputation. It mutates stored memory and may take
          several minutes.
        </DialogContentText>
        <TextField
          select
          fullWidth
          size="small"
          label="Profile (optional)"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          helperText="Leave empty to use the runtime default profile policy."
        >
          <MenuItem value="">(default)</MenuItem>
          {PROFILES.map((p) => (
            <MenuItem key={p} value={p}>
              {p}
            </MenuItem>
          ))}
        </TextField>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose} disabled={props.busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="warning"
          disabled={props.busy}
          startIcon={props.busy ? <CircularProgress size={16} /> : <PlayArrowIcon />}
          onClick={() => props.onConfirm(profile || null)}
        >
          {props.busy ? "Running…" : "Run consolidation"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// --- Report detail drawer --------------------------------------------------------

function ReportDrawer(props: { report: ConsolidationReport | null; onClose: () => void }) {
  const [detail, setDetail] = useState<ConsolidationReport | null>(null);
  const [expandedStage, setExpandedStage] = useState<number | null>(null);

  useEffect(() => {
    setDetail(props.report);
    setExpandedStage(null);
    if (props.report?.id) {
      // Refresh from the detail endpoint; fall back to the row we already have.
      apiGet<ConsolidationReport>(`/consolidation/reports/${props.report.id}`)
        .then(setDetail)
        .catch(() => setDetail(props.report));
    }
  }, [props.report]);

  const r = detail;
  return (
    <Drawer anchor="right" open={!!props.report} onClose={props.onClose}>
      <Box sx={{ width: { xs: 360, sm: 520 }, p: 2 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">Consolidation report</Typography>
          <IconButton onClick={props.onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
        {!r ? (
          <CircularProgress sx={{ mt: 2 }} />
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              <Chip size="small" label={r.status} color={reportStatusColor(r.status)} />
              {r.profile_id && <Chip size="small" variant="outlined" label={r.profile_id} />}
              <Typography variant="body2" color="text.secondary">
                started {relativeTime(r.started_at)} · duration{" "}
                {formatDuration(r.started_at, r.completed_at)}
              </Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-all" }}>
              {r.id}
            </Typography>
            {r.error && <Alert severity="error">{r.error}</Alert>}

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Summary
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gap: 1,
                  gridTemplateColumns: "repeat(3, 1fr)",
                }}
              >
                {SUMMARY_LABELS.map(([key, label]) => (
                  <Card variant="outlined" key={key}>
                    <CardContent sx={{ p: 1, "&:last-child": { pb: 1 } }}>
                      <Typography variant="caption" color="text.secondary" display="block">
                        {label}
                      </Typography>
                      <Typography variant="body1">
                        {(r.summary?.[key] as number | undefined) ?? 0}
                      </Typography>
                    </CardContent>
                  </Card>
                ))}
              </Box>
              {r.summary?.weight_adjustments &&
                Object.keys(r.summary.weight_adjustments).length > 0 && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      Weight adjustments
                    </Typography>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {Object.entries(r.summary.weight_adjustments).map(([k, v]) => (
                        <Chip
                          key={k}
                          size="small"
                          variant="outlined"
                          label={`${k}: ${Number(v).toFixed(3)}`}
                        />
                      ))}
                    </Stack>
                  </Box>
                )}
            </Box>

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Stages
              </Typography>
              <Paper variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>#</TableCell>
                      <TableCell>Stage</TableCell>
                      <TableCell align="right">Processed</TableCell>
                      <TableCell align="right">Affected</TableCell>
                      <TableCell align="right">LLM</TableCell>
                      <TableCell align="right">Time</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(r.stage_results ?? []).map((s) => {
                      const hasDetails = s.details && Object.keys(s.details).length > 0;
                      const expanded = expandedStage === s.stage;
                      return (
                        <React.Fragment key={s.stage}>
                          <TableRow hover>
                            <TableCell>{s.stage}</TableCell>
                            <TableCell>{s.name || STAGE_NAMES[s.stage] || ""}</TableCell>
                            <TableCell align="right">{s.items_processed}</TableCell>
                            <TableCell align="right">{s.items_affected}</TableCell>
                            <TableCell align="right">{s.llm_calls_made}</TableCell>
                            <TableCell align="right">{formatMs(s.duration_ms)}</TableCell>
                            <TableCell padding="checkbox">
                              {hasDetails && (
                                <IconButton
                                  size="small"
                                  onClick={() =>
                                    setExpandedStage(expanded ? null : s.stage)
                                  }
                                >
                                  {expanded ? (
                                    <ExpandLessIcon fontSize="small" />
                                  ) : (
                                    <ExpandMoreIcon fontSize="small" />
                                  )}
                                </IconButton>
                              )}
                            </TableCell>
                          </TableRow>
                          {expanded && (
                            <TableRow>
                              <TableCell colSpan={7} sx={{ bgcolor: "action.hover" }}>
                                <Box
                                  component="pre"
                                  sx={{
                                    m: 0,
                                    fontSize: 12,
                                    whiteSpace: "pre-wrap",
                                    wordBreak: "break-word",
                                  }}
                                >
                                  {JSON.stringify(s.details, null, 2)}
                                </Box>
                              </TableCell>
                            </TableRow>
                          )}
                        </React.Fragment>
                      );
                    })}
                    {(r.stage_results ?? []).length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7}>No stage results recorded.</TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </Paper>
            </Box>
          </Stack>
        )}
      </Box>
    </Drawer>
  );
}

// --- Reports tab -----------------------------------------------------------------

function ReportsTab(props: {
  reports: ConsolidationReport[];
  loading: boolean;
  onSelect: (report: ConsolidationReport) => void;
}) {
  if (props.loading) return <CircularProgress />;
  return (
    <Paper variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Started</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Duration</TableCell>
            <TableCell>Profile</TableCell>
            <TableCell align="right">Merged</TableCell>
            <TableCell align="right">Strengthened</TableCell>
            <TableCell align="right">Decayed</TableCell>
            <TableCell align="right">Promoted</TableCell>
            <TableCell align="right">Suggested</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {props.reports.map((r) => (
            <TableRow
              key={r.id}
              hover
              sx={{ cursor: "pointer" }}
              onClick={() => props.onSelect(r)}
            >
              <TableCell>{relativeTime(r.started_at)}</TableCell>
              <TableCell>
                <Tooltip title={r.error ?? ""}>
                  <Chip size="small" label={r.status} color={reportStatusColor(r.status)} />
                </Tooltip>
              </TableCell>
              <TableCell>{formatDuration(r.started_at, r.completed_at)}</TableCell>
              <TableCell>{r.profile_id ?? "—"}</TableCell>
              <TableCell align="right">{r.summary?.duplicates_merged ?? 0}</TableCell>
              <TableCell align="right">{r.summary?.facts_strengthened ?? 0}</TableCell>
              <TableCell align="right">{r.summary?.facts_decayed ?? 0}</TableCell>
              <TableCell align="right">{r.summary?.episodic_promoted ?? 0}</TableCell>
              <TableCell align="right">{r.summary?.procedures_suggested ?? 0}</TableCell>
            </TableRow>
          ))}
          {props.reports.length === 0 && (
            <TableRow>
              <TableCell colSpan={9}>
                No consolidation runs yet. Trigger one with "Run consolidation".
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
}

// --- Suggestions tab ---------------------------------------------------------------

const SUGGESTION_FILTERS = ["pending", "approved", "rejected", "all"] as const;
type SuggestionFilter = (typeof SUGGESTION_FILTERS)[number];

function SuggestionsTab(props: { onChanged: () => void }) {
  const authority = useAuthority();
  const canReview = authority >= 70;
  const [filter, setFilter] = useState<SuggestionFilter>("pending");
  const [suggestions, setSuggestions] = useState<ProcedureSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ title: string; body: any } | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    apiGet<ProcedureSuggestion[]>(
      "/consolidation/suggestions",
      filter === "all" ? undefined : { approval_status: filter },
    )
      .then((r) => setSuggestions(Array.isArray(r) ? r : []))
      .catch(() => setSuggestions([]))
      .finally(() => setLoading(false));
  }, [filter]);
  useEffect(() => load(), [load]);

  const resolve = async (id: string, status: "approved" | "rejected") => {
    setError(null);
    setBusyId(id);
    try {
      await apiSend("PATCH", `/consolidation/suggestions/${id}`, {
        approval_status: status,
      });
      load();
      props.onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={filter}
          onChange={(_, v) => v && setFilter(v as SuggestionFilter)}
        >
          {SUGGESTION_FILTERS.map((f) => (
            <ToggleButton key={f} value={f}>
              {f}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {!canReview && (
          <Typography variant="caption" color="text.secondary">
            Approving or rejecting suggestions requires authority &ge; 70.
          </Typography>
        )}
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {loading ? (
        <CircularProgress />
      ) : (
        <Stack spacing={2}>
          {suggestions.length === 0 && (
            <Typography color="text.secondary">
              No {filter === "all" ? "" : `${filter} `}procedure suggestions.
            </Typography>
          )}
          {suggestions.map((s) => {
            const tools = parseJsonSafe(s.tool_sequence_json);
            const draftBody = parseJsonSafe(s.draft_procedure_json ?? null);
            const pending = s.approval_status === "pending";
            return (
              <Card key={s.id} variant="outlined">
                <CardContent>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Chip
                      size="small"
                      label={s.approval_status}
                      color={suggestionStatusColor(s.approval_status)}
                    />
                    <Typography variant="subtitle2">{s.pattern_description}</Typography>
                  </Stack>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.5 }}
                  >
                    confidence {(Number(s.confidence) * 100).toFixed(0)}% · seen in{" "}
                    {s.sessions_observed} session{s.sessions_observed === 1 ? "" : "s"} ·
                    suggested {relativeTime(s.created_at)}
                  </Typography>
                  {Array.isArray(tools) && tools.length > 0 && (
                    <Stack
                      direction="row"
                      spacing={1}
                      flexWrap="wrap"
                      useFlexGap
                      sx={{ mt: 1 }}
                    >
                      {tools.map((t: any, i: number) => (
                        <Chip
                          key={i}
                          size="small"
                          variant="outlined"
                          label={typeof t === "string" ? t : JSON.stringify(t)}
                        />
                      ))}
                    </Stack>
                  )}
                  <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
                    {draftBody !== null && (
                      <Button
                        size="small"
                        onClick={() =>
                          setDraft({ title: s.pattern_description, body: draftBody })
                        }
                      >
                        View draft procedure
                      </Button>
                    )}
                    {canReview && pending && (
                      <>
                        <Button
                          size="small"
                          variant="contained"
                          color="success"
                          disabled={busyId === s.id}
                          onClick={() => resolve(s.id, "approved")}
                        >
                          Approve
                        </Button>
                        <Button
                          size="small"
                          variant="outlined"
                          color="error"
                          disabled={busyId === s.id}
                          onClick={() => resolve(s.id, "rejected")}
                        >
                          Reject
                        </Button>
                      </>
                    )}
                  </Stack>
                </CardContent>
              </Card>
            );
          })}
        </Stack>
      )}
      <Dialog open={!!draft} onClose={() => setDraft(null)} fullWidth maxWidth="md">
        <DialogTitle>Draft procedure — {draft?.title}</DialogTitle>
        <DialogContent>
          <Box
            component="pre"
            sx={{ m: 0, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {JSON.stringify(draft?.body, null, 2)}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDraft(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// --- Page ---------------------------------------------------------------------------

export const ConsolidationPage: React.FC = () => {
  const authority = useAuthority();
  const [tab, setTab] = useState(0);

  const [status, setStatus] = useState<ConsolidationStatus>({ running: false });
  const [reports, setReports] = useState<ConsolidationReport[]>([]);
  const [reportsLoading, setReportsLoading] = useState(true);
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [selected, setSelected] = useState<ConsolidationReport | null>(null);

  const [runOpen, setRunOpen] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const wasRunning = useRef(false);

  const loadReports = useCallback(() => {
    setReportsLoading(true);
    apiGet<ConsolidationReport[]>("/consolidation/reports", { limit: 25 })
      .then((r) => setReports(Array.isArray(r) ? r : []))
      .catch(() => setReports([]))
      .finally(() => setReportsLoading(false));
  }, []);

  const loadPendingCount = useCallback(() => {
    apiGet<ProcedureSuggestion[]>("/consolidation/suggestions", {
      approval_status: "pending",
    })
      .then((r) => setPendingCount(Array.isArray(r) ? r.length : 0))
      .catch(() => setPendingCount(null));
  }, []);

  const loadStatus = useCallback(() => {
    apiGet<ConsolidationStatus>("/consolidation/status")
      .then((s) => setStatus(s && typeof s === "object" ? s : { running: false }))
      .catch(() => {
        /* keep last known status */
      });
  }, []);

  // Initial load + status polling (5s).
  useEffect(() => {
    loadReports();
    loadPendingCount();
    loadStatus();
    const timer = window.setInterval(loadStatus, 5000);
    return () => window.clearInterval(timer);
  }, [loadReports, loadPendingCount, loadStatus]);

  // When a run finishes (running -> idle), refresh reports + suggestions.
  useEffect(() => {
    if (wasRunning.current && !status.running) {
      loadReports();
      loadPendingCount();
    }
    wasRunning.current = !!status.running;
  }, [status.running, loadReports, loadPendingCount]);

  const runConsolidation = async (profileId: string | null) => {
    setRunError(null);
    setRunBusy(true);
    try {
      const report = await apiSend<ConsolidationReport>(
        "POST",
        "/consolidation/run",
        { profile_id: profileId },
      );
      setRunOpen(false);
      setToast(`Consolidation ${report?.status ?? "completed"}.`);
      loadReports();
      loadPendingCount();
      loadStatus();
      if (report?.id) setSelected(report);
    } catch (e) {
      setRunError(friendlyRunError((e as Error).message));
    } finally {
      setRunBusy(false);
    }
  };

  const lastRunAt =
    status.last_run_at ?? reports[0]?.completed_at ?? reports[0]?.started_at;
  const currentStage = status.current_stage ?? 0;

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Consolidation</Typography>
        {authority >= 90 && (
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            disabled={status.running || runBusy}
            onClick={() => {
              setRunError(null);
              setRunOpen(true);
            }}
          >
            Run consolidation
          </Button>
        )}
      </Stack>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          mb: 3,
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
        }}
      >
        <Card variant="outlined">
          <CardContent>
            <Typography variant="caption" color="text.secondary">
              Status
            </Typography>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
              <Chip
                size="small"
                label={status.running ? "Running" : "Idle"}
                color={status.running ? "info" : "default"}
              />
              {status.running && currentStage > 0 && (
                <Typography variant="body2" color="text.secondary">
                  Stage {currentStage}/{STAGE_COUNT}
                  {STAGE_NAMES[currentStage] ? ` — ${STAGE_NAMES[currentStage]}` : ""}
                </Typography>
              )}
            </Stack>
            {status.running && (
              <LinearProgress
                sx={{ mt: 1.5 }}
                variant={currentStage > 0 ? "determinate" : "indeterminate"}
                value={Math.min((currentStage / STAGE_COUNT) * 100, 100)}
              />
            )}
            {status.running && status.started_at && (
              <Typography variant="caption" color="text.secondary">
                started {relativeTime(status.started_at)}
              </Typography>
            )}
          </CardContent>
        </Card>
        <Card variant="outlined">
          <CardContent>
            <Typography variant="caption" color="text.secondary">
              Last run
            </Typography>
            <Typography variant="h6">{relativeTime(lastRunAt)}</Typography>
            {reports[0] && (
              <Chip
                size="small"
                label={reports[0].status}
                color={reportStatusColor(reports[0].status)}
              />
            )}
          </CardContent>
        </Card>
        <Card variant="outlined">
          <CardContent>
            <Typography variant="caption" color="text.secondary">
              Pending suggestions
            </Typography>
            <Typography variant="h6">{pendingCount ?? "—"}</Typography>
            <Typography variant="caption" color="text.secondary">
              procedure drafts awaiting review
            </Typography>
          </CardContent>
        </Card>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Reports" />
        <Tab
          label={`Procedure suggestions${
            pendingCount ? ` (${pendingCount})` : ""
          }`}
        />
      </Tabs>
      {tab === 0 && (
        <ReportsTab
          reports={reports}
          loading={reportsLoading}
          onSelect={setSelected}
        />
      )}
      {tab === 1 && <SuggestionsTab onChanged={loadPendingCount} />}

      <ReportDrawer report={selected} onClose={() => setSelected(null)} />
      <RunDialog
        open={runOpen}
        busy={runBusy}
        error={runError}
        onClose={() => setRunOpen(false)}
        onConfirm={runConsolidation}
      />
      <Snackbar
        open={!!toast}
        autoHideDuration={5000}
        onClose={() => setToast(null)}
        message={toast ?? ""}
      />
    </Box>
  );
};

export default ConsolidationPage;
