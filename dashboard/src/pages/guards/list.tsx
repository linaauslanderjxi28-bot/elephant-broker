// Guards page — 4 tabs: Activity, Rules, Config, Approvals.
//
// Activity: cross-session guard events (ClickHouse) + summary cards.
// Rules: unified rule list (builtin/profile/procedure/custom) with CRUD on
//        custom rules (create >= 70).
// Config: per-profile guard strictness settings (view; edit >= 70).
// Approvals: cross-session pending HITL approvals with approve/reject (>= 50).

import React, { useCallback, useEffect, useState } from "react";
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
  DialogTitle,
  MenuItem,
  Paper,
  Slider,
  Stack,
  Switch,
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
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import IconButton from "@mui/material/IconButton";
import {
  apiGet,
  apiSend,
  DECISION_DOMAINS,
  guardOutcomeColor,
  relativeTime,
  TIME_RANGES,
  type TimeRange,
  useAuthority,
} from "../home/dashboardApi";

const PATTERN_TYPES = ["keyword", "phrase", "regex", "tool_target"];
const OUTCOMES = ["block", "require_approval", "warn", "log_only"];
const SEVERITIES = ["critical", "high", "medium", "low"];
const PROFILES = ["coding", "research", "managerial", "worker", "personal_assistant"];

// --- Tab 1: Activity ---------------------------------------------------------

function ActivityTab() {
  const [range, setRange] = useState<TimeRange>("24h");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiGet<any>("/dashboard/guards/activity", { time_range: range })
      .then(setData)
      .catch(() => setData({ events: [] }))
      .finally(() => setLoading(false));
  }, [range]);

  const events = data?.events ?? data?.items ?? [];
  const cards = [
    ["Checks", data?.total_checks],
    ["Triggers", data?.triggers],
    ["Near-misses", data?.near_misses],
    ["Pending approvals", data?.pending_approvals],
  ];

  return (
    <Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
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
      <Box
        sx={{
          display: "grid",
          gap: 2,
          mb: 2,
          gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, 1fr)" },
        }}
      >
        {cards.map(([label, value]) => (
          <Card variant="outlined" key={String(label)}>
            <CardContent>
              <Typography variant="caption" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h5">{value ?? 0}</Typography>
            </CardContent>
          </Card>
        ))}
      </Box>
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Session</TableCell>
                <TableCell>Action</TableCell>
                <TableCell>Domain</TableCell>
                <TableCell>Outcome</TableCell>
                <TableCell>Rule</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((e: any, i: number) => (
                <TableRow key={i}>
                  <TableCell>{relativeTime(e.timestamp)}</TableCell>
                  <TableCell>{e.session_key ?? ""}</TableCell>
                  <TableCell>{e.action ?? ""}</TableCell>
                  <TableCell>
                    {e.domain && <Chip size="small" label={e.domain} />}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={e.outcome ?? ""}
                      color={guardOutcomeColor(e.outcome)}
                    />
                  </TableCell>
                  <TableCell>{e.rule_id ?? ""}</TableCell>
                </TableRow>
              ))}
              {events.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6}>No guard events.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  );
}

// --- Tab 2: Rules ------------------------------------------------------------

function RuleForm(props: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  initial?: any;
}) {
  const editing = !!props.initial?.id;
  const [form, setForm] = useState<any>(
    props.initial ?? {
      name: "",
      description: "",
      pattern_type: "keyword",
      pattern: "",
      outcome: "require_approval",
      decision_domain: DECISION_DOMAINS[0],
      severity: "medium",
      enabled: true,
    },
  );
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (props.open)
      setForm(
        props.initial ?? {
          name: "",
          description: "",
          pattern_type: "keyword",
          pattern: "",
          outcome: "require_approval",
          decision_domain: DECISION_DOMAINS[0],
          severity: "medium",
          enabled: true,
        },
      );
  }, [props.open, props.initial]);

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const submit = async () => {
    setErr(null);
    try {
      if (editing) {
        await apiSend("PUT", `/dashboard/guards/rules/${form.id}`, form);
      } else {
        await apiSend("POST", "/dashboard/guards/rules", form);
      }
      props.onSaved();
      props.onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>{editing ? "Edit" : "Create"} custom rule</DialogTitle>
      <DialogContent>
        {err && <Alert severity="error">{err}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Name"
            value={form.name ?? ""}
            onChange={(e) => set("name", e.target.value)}
          />
          <TextField
            label="Description"
            multiline
            value={form.description ?? ""}
            onChange={(e) => set("description", e.target.value)}
          />
          <TextField
            select
            label="Pattern type"
            value={form.pattern_type}
            onChange={(e) => set("pattern_type", e.target.value)}
          >
            {PATTERN_TYPES.map((p) => (
              <MenuItem key={p} value={p}>
                {p}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Pattern"
            value={form.pattern ?? ""}
            onChange={(e) => set("pattern", e.target.value)}
          />
          <TextField
            select
            label="Domain"
            value={form.decision_domain}
            onChange={(e) => set("decision_domain", e.target.value)}
          >
            {DECISION_DOMAINS.map((d) => (
              <MenuItem key={d} value={d}>
                {d}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Outcome"
            value={form.outcome}
            onChange={(e) => set("outcome", e.target.value)}
          >
            {OUTCOMES.map((o) => (
              <MenuItem key={o} value={o}>
                {o}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Severity"
            value={form.severity}
            onChange={(e) => set("severity", e.target.value)}
          >
            {SEVERITIES.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </TextField>
          <Stack direction="row" alignItems="center">
            <Typography variant="body2">Enabled</Typography>
            <Switch
              checked={!!form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.pattern} onClick={submit}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function RulesTab() {
  const authority = useAuthority();
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<any>(undefined);

  const load = useCallback(() => {
    setLoading(true);
    apiGet<any>("/dashboard/guards/rules")
      .then((r) => setRules(Array.isArray(r) ? r : (r.rules ?? r.items ?? [])))
      .catch(() => setRules([]))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  const sourceColor = (src: string) =>
    src === "custom"
      ? "success"
      : src === "profile"
        ? "info"
        : src === "procedure"
          ? "secondary"
          : "default";

  return (
    <Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        {authority >= 70 && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            Create Rule
          </Button>
        )}
      </Stack>
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Source</TableCell>
                <TableCell>Name / Pattern</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Outcome</TableCell>
                <TableCell>Enabled</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rules.map((r, i) => {
                const src = r.source ?? "builtin";
                const editable = src === "custom" && authority >= 70;
                return (
                  <TableRow key={r.id ?? i}>
                    <TableCell>
                      <Chip size="small" label={src} color={sourceColor(src)} />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {r.name ?? r.id}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {r.pattern}
                      </Typography>
                    </TableCell>
                    <TableCell>{r.pattern_type}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={r.outcome}
                        color={guardOutcomeColor(r.outcome)}
                      />
                    </TableCell>
                    <TableCell>
                      <Switch
                        size="small"
                        checked={r.enabled !== false}
                        disabled={!editable}
                        onChange={async () => {
                          await apiSend(
                            "PUT",
                            `/dashboard/guards/rules/${r.id}`,
                            { enabled: r.enabled === false },
                          );
                          load();
                        }}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {editable && (
                        <>
                          <Button
                            size="small"
                            onClick={() => {
                              setEditing(r);
                              setFormOpen(true);
                            }}
                          >
                            Edit
                          </Button>
                          <IconButton
                            size="small"
                            onClick={async () => {
                              if (!window.confirm("Delete rule?")) return;
                              await apiSend(
                                "DELETE",
                                `/dashboard/guards/rules/${r.id}`,
                              );
                              load();
                            }}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
              {rules.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6}>No rules.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
      <RuleForm
        open={formOpen}
        initial={editing}
        onClose={() => setFormOpen(false)}
        onSaved={load}
      />
    </Box>
  );
}

// --- Tab 3: Config -----------------------------------------------------------

function ConfigTab() {
  const authority = useAuthority();
  const canEdit = authority >= 70;
  const [profile, setProfile] = useState("coding");
  const [cfg, setCfg] = useState<any>(null);

  useEffect(() => {
    apiGet<any>(`/profiles/${profile}/resolve`)
      .then((r) => setCfg(r?.guard_config ?? r?.guards ?? r ?? {}))
      .catch(() => setCfg({}));
  }, [profile]);

  const num = (k: string, dflt: number) =>
    cfg?.[k] ?? dflt;

  return (
    <Box>
      <TextField
        select
        size="small"
        label="Profile"
        value={profile}
        onChange={(e) => setProfile(e.target.value)}
        sx={{ minWidth: 220, mb: 2 }}
      >
        {PROFILES.map((p) => (
          <MenuItem key={p} value={p}>
            {p}
          </MenuItem>
        ))}
      </TextField>
      {!cfg ? (
        <CircularProgress />
      ) : (
        <Stack spacing={3} sx={{ maxWidth: 560 }}>
          <Box>
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Typography variant="body2">Force constraint injection</Typography>
              <Switch
                checked={cfg.force_constraint_injection !== false}
                disabled={!canEdit}
              />
            </Stack>
            <Typography variant="caption" color="text.secondary">
              Inject safety constraints into the agent prompt every turn.
            </Typography>
          </Box>
          {[
            ["semantic_threshold", "Semantic similarity threshold", 0.7],
            ["bm25_weight", "BM25 weight", 0.4],
            ["near_miss_threshold", "Near-miss threshold", 0.6],
          ].map(([key, label, dflt]) => (
            <Box key={String(key)}>
              <Typography variant="body2">
                {label}: {num(key as string, dflt as number)}
              </Typography>
              <Slider
                value={num(key as string, dflt as number)}
                min={0}
                max={1}
                step={0.05}
                disabled={!canEdit}
              />
            </Box>
          ))}
          <TextField
            type="number"
            label="Max LLM escalations per turn"
            value={num("max_llm_escalations", 2)}
            disabled={!canEdit}
            sx={{ maxWidth: 260 }}
          />
        </Stack>
      )}
      {!canEdit && (
        <Alert severity="info" sx={{ mt: 2 }}>
          Editing guard configuration requires authority &ge; 70.
        </Alert>
      )}
    </Box>
  );
}

// --- Tab 4: Approvals --------------------------------------------------------

function ApprovalsTab() {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    apiGet<any>("/dashboard/guards/approvals/pending")
      .then((r) =>
        setApprovals(Array.isArray(r) ? r : (r.pending ?? r.items ?? [])),
      )
      .catch(() => setApprovals([]))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  const resolve = async (id: string, status: "approved" | "rejected") => {
    let reason: string | null = "";
    if (status === "rejected") {
      reason = window.prompt("Rejection reason");
      if (reason === null) return;
    }
    await apiSend("PATCH", `/guards/approvals/${id}`, { status, reason });
    load();
  };

  if (loading) return <CircularProgress />;
  return (
    <Stack spacing={2}>
      {approvals.length === 0 && (
        <Typography color="text.secondary">No pending approvals.</Typography>
      )}
      {approvals.map((a, i) => {
        const id = String(a.request_id ?? a.id ?? a);
        return (
          <Card key={id} variant="outlined">
            <CardContent>
              <Typography variant="subtitle2">
                {a.action ?? a.summary ?? id}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {a.session_key ?? ""} ·{" "}
                {a.domain && <Chip size="small" label={a.domain} />} · requested{" "}
                {relativeTime(a.requested_at ?? a.created_at)}
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                <Button
                  size="small"
                  variant="contained"
                  color="success"
                  onClick={() => resolve(id, "approved")}
                >
                  Approve
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  onClick={() => resolve(id, "rejected")}
                >
                  Reject
                </Button>
              </Stack>
            </CardContent>
          </Card>
        );
      })}
    </Stack>
  );
}

export const GuardsPage: React.FC = () => {
  const authority = useAuthority();
  const [tab, setTab] = useState(0);

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Guards
      </Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Activity" />
        <Tab label="Rules" />
        <Tab label="Config" />
        {authority >= 50 && <Tab label="Approvals" />}
      </Tabs>
      {tab === 0 && <ActivityTab />}
      {tab === 1 && <RulesTab />}
      {tab === 2 && <ConfigTab />}
      {tab === 3 && authority >= 50 && <ApprovalsTab />}
    </Box>
  );
};

export default GuardsPage;
