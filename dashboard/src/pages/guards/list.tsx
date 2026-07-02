// Guards page — 4 tabs: Activity, Rules, Config, Approvals.
//
// Activity: cross-session guard events (ClickHouse) + summary cards.
// Rules: unified rule list (builtin/profile/procedure/custom) with CRUD on
//        custom rules (create >= 70).
// Config: org profile-override editor for guard policy — select org + profile,
//         see resolved values, edits build an override diff persisted via
//         PUT/DELETE /admin/profiles/overrides/{org}/{profile} (edit >= 90).
// Approvals: cross-session pending HITL approvals with approve/reject (>= 50).

import React, { useCallback, useEffect, useMemo, useState } from "react";
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

// --- Tab 3: Config (org profile-override editor) -------------------------------
//
// Backend contract (elephantbroker/api/routes/admin.py):
//   GET    /admin/profiles/overrides/{org_id}
//     -> [{profile_id, overrides, updated_at, updated_by_actor_id}]
//   PUT    /admin/profiles/overrides/{org_id}/{profile_id}   body {overrides: dict}
//   DELETE /admin/profiles/overrides/{org_id}/{profile_id}
// Override dicts are sparse diffs vs the base profile: top-level keys must be
// ProfilePolicy fields, nested keys must be fields of the nested model — so a
// guard edit ships as {"guards": {"bm25_block_threshold": 0.9}}.
// Resolved values come from GET /profiles/{profile_id} (which resolves with the
// gateway's configured org); org/profile lists from /dashboard/organizations
// and /dashboard/profiles.

/** Editable guard-policy fields (paths into ProfilePolicy, see schemas/profile.py GuardPolicy). */
const GUARD_FIELDS: Array<{
  path: string;
  label: string;
  help?: string;
  kind: "switch" | "slider" | "int" | "select";
  dflt: any;
  options?: string[];
  min?: number;
}> = [
  {
    path: "guards.force_system_constraint_injection",
    label: "Force constraint injection",
    help: "Inject red-line system constraints into the agent prompt every turn.",
    kind: "switch",
    dflt: true,
  },
  {
    path: "guards.preflight_check_strictness",
    label: "Preflight check strictness",
    kind: "select",
    options: ["low", "medium", "high"],
    dflt: "medium",
  },
  {
    path: "guards.bm25_block_threshold",
    label: "BM25 block threshold",
    help: "Lexical match score at or above which an action is blocked.",
    kind: "slider",
    dflt: 0.85,
  },
  {
    path: "guards.bm25_warn_threshold",
    label: "BM25 warn threshold",
    help: "Lexical match score at or above which a warning is raised.",
    kind: "slider",
    dflt: 0.6,
  },
  {
    path: "guards.semantic_similarity_threshold",
    label: "Semantic similarity threshold",
    help: "Embedding similarity to red-line exemplars that triggers the guard.",
    kind: "slider",
    dflt: 0.8,
  },
  {
    path: "guards.llm_escalation_enabled",
    label: "LLM escalation",
    help: "Escalate ambiguous guard checks to an LLM (slower, more accurate).",
    kind: "switch",
    dflt: false,
  },
  {
    path: "guards.near_miss_escalation_threshold",
    label: "Near-miss escalation threshold",
    help: "Near-misses within the window before escalation.",
    kind: "int",
    min: 1,
    dflt: 3,
  },
  {
    path: "guards.near_miss_window_turns",
    label: "Near-miss window (turns)",
    kind: "int",
    min: 1,
    dflt: 5,
  },
  {
    path: "guards.load_procedure_redline_bindings",
    label: "Load procedure red-line bindings",
    help: "Load red-line rules bound to procedures into the guard pipeline.",
    kind: "switch",
    dflt: true,
  },
];

function getPath(obj: any, path: string): any {
  let cur = obj;
  for (const part of path.split(".")) {
    if (cur === null || cur === undefined || typeof cur !== "object")
      return undefined;
    cur = cur[part];
  }
  return cur;
}

function setPath(obj: any, path: string, value: any): any {
  const parts = path.split(".");
  const out = { ...(obj ?? {}) };
  let cur: any = out;
  for (let idx = 0; idx < parts.length - 1; idx++) {
    cur[parts[idx]] =
      typeof cur[parts[idx]] === "object" && cur[parts[idx]] !== null
        ? { ...cur[parts[idx]] }
        : {};
    cur = cur[parts[idx]];
  }
  cur[parts[parts.length - 1]] = value;
  return out;
}

function deletePath(obj: any, path: string): any {
  const parts = path.split(".");
  const out = { ...(obj ?? {}) };
  const chain: any[] = [out];
  let cur: any = out;
  for (let idx = 0; idx < parts.length - 1; idx++) {
    if (typeof cur[parts[idx]] !== "object" || cur[parts[idx]] === null)
      return out;
    cur[parts[idx]] = { ...cur[parts[idx]] };
    cur = cur[parts[idx]];
    chain.push(cur);
  }
  delete cur[parts[parts.length - 1]];
  // Prune now-empty parent objects so the diff stays sparse.
  for (let idx = chain.length - 1; idx > 0; idx--) {
    if (Object.keys(chain[idx]).length === 0) delete chain[idx - 1][parts[idx - 1]];
  }
  return out;
}

function deepEqual(a: any, b: any): boolean {
  if (a === b) return true;
  if (
    typeof a !== "object" ||
    typeof b !== "object" ||
    a === null ||
    b === null
  )
    return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every((k) => deepEqual(a[k], b[k]));
}

function ConfigTab() {
  const authority = useAuthority();
  const canEdit = authority >= 90;
  const [orgs, setOrgs] = useState<any[] | null>(null); // null => loading
  const [org, setOrg] = useState("");
  const [profiles, setProfiles] = useState<string[]>(PROFILES);
  const [profile, setProfile] = useState("coding");
  const [base, setBase] = useState<any>(null); // resolved policy from /profiles/{id}
  const [saved, setSaved] = useState<any>({}); // override as stored on the server
  const [savedMeta, setSavedMeta] = useState<any>(null);
  const [pending, setPending] = useState<any>({}); // override being edited
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ovErr, setOvErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Load org + profile option lists once.
  useEffect(() => {
    apiGet<any>("/dashboard/organizations")
      .then((r) => {
        const list = r?.organizations ?? [];
        setOrgs(list);
        if (list.length > 0)
          setOrg((cur) => cur || String(list[0].org_id ?? ""));
      })
      .catch(() => setOrgs([]));
    apiGet<any>("/dashboard/profiles")
      .then((r) => {
        const names = (r?.profiles ?? [])
          .map((p: any) => String(p.profile_id ?? ""))
          .filter(Boolean);
        if (names.length > 0) setProfiles(names);
      })
      .catch(() => {
        /* keep static fallback */
      });
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    setOvErr(null);
    setNotice(null);
    const overridesPromise: Promise<{ list: any[]; error: string | null }> = org
      ? apiGet<any>(`/admin/profiles/overrides/${org}`).then(
          (r) => ({ list: Array.isArray(r) ? r : [], error: null }),
          (e) => ({ list: [], error: (e as Error).message }),
        )
      : Promise.resolve({ list: [], error: null });
    Promise.all([apiGet<any>(`/profiles/${profile}`), overridesPromise])
      .then(([policy, ov]) => {
        setBase(policy ?? {});
        setOvErr(ov.error);
        const entry = ov.list.find((o: any) => o.profile_id === profile);
        const overrides = entry?.overrides ?? {};
        setSaved(overrides);
        setSavedMeta(entry ?? null);
        setPending(JSON.parse(JSON.stringify(overrides)));
      })
      .catch((e) => {
        setBase(null);
        setErr((e as Error).message);
      })
      .finally(() => setLoading(false));
  }, [org, profile]);
  useEffect(() => load(), [load]);

  const dirty = useMemo(() => !deepEqual(pending, saved), [pending, saved]);

  // Keys in the override that this tab does not edit (preserved verbatim on save).
  const extraKeys = useMemo(() => {
    const guardKeys = new Set(GUARD_FIELDS.map((f) => f.path.split(".")[1]));
    const keys = Object.keys(pending ?? {}).filter((k) => k !== "guards");
    for (const gk of Object.keys(pending?.guards ?? {})) {
      if (!guardKeys.has(gk)) keys.push(`guards.${gk}`);
    }
    return keys;
  }, [pending]);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (Object.keys(pending).length === 0) {
        // Empty diff == no override — remove the row instead of storing {}.
        await apiSend("DELETE", `/admin/profiles/overrides/${org}/${profile}`);
      } else {
        await apiSend("PUT", `/admin/profiles/overrides/${org}/${profile}`, {
          overrides: pending,
        });
      }
      load();
      setNotice("Override saved.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const resetToBase = async () => {
    if (
      !window.confirm(
        `Delete the "${profile}" override for this organization? All fields revert to base profile values.`,
      )
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      await apiSend("DELETE", `/admin/profiles/overrides/${org}/${profile}`);
      load();
      setNotice("Override removed — profile now uses base values.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const valueOf = (f: (typeof GUARD_FIELDS)[number]) => {
    const pv = getPath(pending, f.path);
    if (pv !== undefined) return pv;
    const bv = getPath(base, f.path);
    return bv !== undefined ? bv : f.dflt;
  };

  /** null | "overridden" (saved) | "edited" (unsaved change) | "resets on save". */
  const fieldState = (f: (typeof GUARD_FIELDS)[number]): string | null => {
    const pv = getPath(pending, f.path);
    const sv = getPath(saved, f.path);
    if (pv === undefined && sv === undefined) return null;
    if (pv === undefined) return "resets on save";
    if (deepEqual(pv, sv)) return "overridden";
    return "edited";
  };

  const disabled = !canEdit || !org || busy;
  const setField = (path: string, value: any) =>
    setPending((p: any) => setPath(p, path, value));

  const renderField = (f: (typeof GUARD_FIELDS)[number]) => {
    const value = valueOf(f);
    const state = fieldState(f);
    const chip = state && (
      <Chip
        size="small"
        label={state}
        color={state === "overridden" ? "info" : "warning"}
        onDelete={
          canEdit && !busy && getPath(pending, f.path) !== undefined
            ? () => setPending((p: any) => deletePath(p, f.path))
            : undefined
        }
      />
    );
    if (f.kind === "switch") {
      return (
        <Box key={f.path}>
          <Stack direction="row" alignItems="center" justifyContent="space-between">
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2">{f.label}</Typography>
              {chip}
            </Stack>
            <Switch
              checked={!!value}
              disabled={disabled}
              onChange={(e) => setField(f.path, e.target.checked)}
            />
          </Stack>
          {f.help && (
            <Typography variant="caption" color="text.secondary">
              {f.help}
            </Typography>
          )}
        </Box>
      );
    }
    if (f.kind === "slider") {
      return (
        <Box key={f.path}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="body2">
              {f.label}: {Number(value).toFixed(2)}
            </Typography>
            {chip}
          </Stack>
          <Slider
            value={Number(value)}
            min={0}
            max={1}
            step={0.05}
            disabled={disabled}
            onChange={(_, v) => setField(f.path, v as number)}
          />
          {f.help && (
            <Typography variant="caption" color="text.secondary">
              {f.help}
            </Typography>
          )}
        </Box>
      );
    }
    if (f.kind === "select") {
      return (
        <Box key={f.path}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <TextField
              select
              size="small"
              label={f.label}
              value={String(value)}
              disabled={disabled}
              onChange={(e) => setField(f.path, e.target.value)}
              sx={{ minWidth: 260 }}
            >
              {(f.options ?? []).map((o) => (
                <MenuItem key={o} value={o}>
                  {o}
                </MenuItem>
              ))}
            </TextField>
            {chip}
          </Stack>
          {f.help && (
            <Typography variant="caption" color="text.secondary">
              {f.help}
            </Typography>
          )}
        </Box>
      );
    }
    // int
    return (
      <Box key={f.path}>
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            type="number"
            size="small"
            label={f.label}
            value={value ?? ""}
            disabled={disabled}
            inputProps={{ min: f.min ?? 0 }}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (Number.isNaN(v)) {
                // Clearing the box removes the override for this field.
                setPending((p: any) => deletePath(p, f.path));
              } else {
                setField(f.path, Math.max(f.min ?? 0, v));
              }
            }}
            sx={{ maxWidth: 260 }}
          />
          {chip}
        </Stack>
        {f.help && (
          <Typography variant="caption" color="text.secondary">
            {f.help}
          </Typography>
        )}
      </Box>
    );
  };

  return (
    <Box>
      {orgs !== null && orgs.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No organizations registered. Profile overrides are stored per
          organization — create one first (Settings, or the <code>ebrun</code>{" "}
          CLI). Base profile values are shown read-only below.
        </Alert>
      )}
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        sx={{ mb: 2, flexWrap: "wrap", rowGap: 1 }}
      >
        <TextField
          select
          size="small"
          label="Organization"
          value={org}
          onChange={(e) => setOrg(e.target.value)}
          disabled={!orgs || orgs.length === 0}
          sx={{ minWidth: 220 }}
        >
          {(orgs ?? []).map((o: any) => (
            <MenuItem key={String(o.org_id)} value={String(o.org_id)}>
              {o.display_label || o.name || o.org_id}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Profile"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          sx={{ minWidth: 220 }}
        >
          {profiles.map((p) => (
            <MenuItem key={p} value={p}>
              {p}
            </MenuItem>
          ))}
        </TextField>
        <Box sx={{ flexGrow: 1 }} />
        {canEdit && (
          <>
            <Button
              variant="contained"
              disabled={!org || busy || loading || !dirty}
              onClick={save}
            >
              Save override
            </Button>
            <Button
              variant="outlined"
              color="error"
              disabled={!org || busy || loading || !savedMeta}
              onClick={resetToBase}
            >
              Reset to base
            </Button>
          </>
        )}
      </Stack>
      {err && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErr(null)}>
          {err}
        </Alert>
      )}
      {ovErr && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Could not load existing overrides for this organization: {ovErr}
        </Alert>
      )}
      {notice && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {savedMeta?.updated_at && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mb: 2 }}
        >
          Override last updated {relativeTime(savedMeta.updated_at)}
          {savedMeta.updated_by_actor_id
            ? ` by ${savedMeta.updated_by_actor_id}`
            : ""}
        </Typography>
      )}
      {loading ? (
        <CircularProgress />
      ) : base ? (
        <Stack spacing={3} sx={{ maxWidth: 640 }}>
          {GUARD_FIELDS.map(renderField)}
          {extraKeys.length > 0 && (
            <Typography variant="caption" color="text.secondary">
              This override also sets: {extraKeys.join(", ")} (not editable
              here; preserved on save).
            </Typography>
          )}
        </Stack>
      ) : null}
      {!canEdit && (
        <Alert severity="info" sx={{ mt: 2 }}>
          Editing org profile overrides requires authority &ge; 90.
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
      {approvals.map((a) => {
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
