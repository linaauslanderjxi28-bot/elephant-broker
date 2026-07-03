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
  guardOutcomeColor,
  relativeTime,
  TIME_RANGES,
  type TimeRange,
  useAuthority,
} from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";
import { formatRelativeTime, humanizeEnum } from "../../lib/format";
import { shortId } from "../../lib/labels";

// StaticRule.pattern_type (schemas/guards.py::StaticRulePatternType).
const PATTERN_TYPES = ["keyword", "phrase", "regex", "tool_target"];
// StaticRule.outcome (schemas/guards.py::GuardOutcome). "log_only" is NOT a
// valid outcome — the earlier list (with log_only, no require_evidence/inform)
// produced 422s / wrong colours. These are the real enum values, most-severe
// first; "pass" is omitted (a rule whose action is to pass is meaningless).
const OUTCOMES = ["block", "require_approval", "require_evidence", "warn", "inform"];
const PROFILES = ["coding", "research", "managerial", "worker", "personal_assistant"];

// --- Tab 1: Activity ---------------------------------------------------------

function ActivityTab() {
  const [range, setRange] = useState<TimeRange>("24h");
  const [data, setData] = useState<any>(null);
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    // GET /dashboard/guards/activity -> GuardActivityResponse:
    //   { time_range, triggers, near_misses, by_outcome, recent_events }.
    // The page previously read `events`/`items` + `total_checks`/`pending_approvals`
    // — none of which the endpoint returns — so the table and cards were always
    // empty/zero (guards-profiles-4, gap-3-3/3-4).
    apiGet<any>("/dashboard/guards/activity", { time_range: range })
      .then(setData)
      .catch(() => setData({ recent_events: [] }))
      .finally(() => setLoading(false));
  }, [range]);

  // "Pending approvals" is not in the activity aggregate; it lives in the
  // dedicated pending-approvals endpoint. Fetch its real count instead of the
  // hardwired 0 the card showed before.
  useEffect(() => {
    apiGet<any>("/dashboard/guards/approvals/pending")
      .then((r) =>
        setPendingCount(
          Array.isArray(r) ? r.length : (r?.pending ?? r?.items ?? []).length,
        ),
      )
      .catch(() => setPendingCount(null));
  }, [range]);

  const events: any[] = data?.recent_events ?? [];
  const byOutcome: Record<string, number> = data?.by_outcome ?? {};
  const cards: Array<[string, number | string]> = [
    ["Triggers", data?.triggers ?? 0],
    ["Near-misses", data?.near_misses ?? 0],
    ["Blocked", byOutcome.block ?? 0],
    ["Pending approvals", pendingCount ?? "—"],
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
          <Card variant="outlined" key={label}>
            <CardContent>
              <Typography variant="caption" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h5">{value}</Typography>
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
                <TableCell>Rules</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((e: any, i: number) => {
                // Guard trace payload (engine.py): { outcome, layer, rules[],
                // decision_domain, action_target }. The fields the table needs
                // live under `payload`, not on the event root.
                const p = e.payload ?? {};
                const rules: string[] = Array.isArray(p.rules) ? p.rules : [];
                return (
                  <TableRow key={e.id ?? i}>
                    <TableCell>{relativeTime(e.timestamp)}</TableCell>
                    <TableCell>{e.session_key ?? "—"}</TableCell>
                    <TableCell>{p.action_target ?? "—"}</TableCell>
                    <TableCell>
                      {p.decision_domain && (
                        <Chip
                          size="small"
                          label={humanizeEnum(p.decision_domain)}
                        />
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={humanizeEnum(p.outcome) || "—"}
                        color={guardOutcomeColor(p.outcome)}
                      />
                    </TableCell>
                    <TableCell>
                      {rules.length > 0
                        ? rules.map((r) => humanizeEnum(r)).join(", ")
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
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

// StaticRule (schemas/guards.py): the create body. `id` is REQUIRED (no default)
// and there is NO `name` / `decision_domain` / `severity` field — those were
// phantom form fields silently dropped by Pydantic (guards-profiles-6) while the
// missing `id` 422'd every create (guards-profiles-2). `source`/`org_id` are set
// server-side.
const EMPTY_RULE = {
  id: "",
  description: "",
  pattern_type: "keyword",
  pattern: "",
  outcome: "require_approval",
  enabled: true,
};

function RuleForm(props: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  initial?: any;
}) {
  const editing = !!props.initial?.id;
  const [form, setForm] = useState<any>(props.initial ?? EMPTY_RULE);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (props.open) {
      setForm(props.initial ?? EMPTY_RULE);
      setErr(null);
    }
  }, [props.open, props.initial]);

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      if (editing) {
        // GuardRuleUpdate is `extra="forbid"` — send ONLY its whitelisted
        // fields, never the whole rule object (guards-profiles-3). `id` /
        // `source` / `org_id` are immutable and would 422.
        await apiSend("PUT", `/dashboard/guards/rules/${form.id}`, {
          pattern: form.pattern,
          pattern_type: form.pattern_type,
          outcome: form.outcome,
          description: form.description,
          enabled: form.enabled,
        });
      } else {
        // StaticRule create — include the required `id`.
        await apiSend("POST", "/dashboard/guards/rules", {
          id: form.id,
          pattern_type: form.pattern_type,
          pattern: form.pattern,
          outcome: form.outcome,
          description: form.description,
          enabled: form.enabled,
        });
      }
      props.onSaved();
      props.onClose();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const idValid = editing || /^[a-z0-9][a-z0-9_-]*$/i.test((form.id ?? "").trim());

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>{editing ? "Edit" : "Create"} custom rule</DialogTitle>
      <DialogContent>
        {err && (
          <Alert severity="error" sx={{ mb: 1 }} onClose={() => setErr(null)}>
            {err}
          </Alert>
        )}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Rule ID"
            value={form.id ?? ""}
            disabled={editing}
            onChange={(e) => set("id", e.target.value)}
            helperText={
              editing
                ? "The rule identifier is immutable."
                : "Unique identifier, e.g. no_force_push (letters, digits, _ and -)."
            }
            error={!editing && !!form.id && !idValid}
          />
          <TextField
            label="Description"
            multiline
            value={form.description ?? ""}
            onChange={(e) => set("description", e.target.value)}
            helperText="Human-readable explanation shown to operators."
          />
          <TextField
            select
            label="Pattern type"
            value={form.pattern_type}
            onChange={(e) => set("pattern_type", e.target.value)}
          >
            {PATTERN_TYPES.map((p) => (
              <MenuItem key={p} value={p}>
                {humanizeEnum(p)}
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
            label="Outcome"
            value={form.outcome}
            onChange={(e) => set("outcome", e.target.value)}
          >
            {OUTCOMES.map((o) => (
              <MenuItem key={o} value={o}>
                {humanizeEnum(o)}
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
        <Button onClick={props.onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={busy || !form.pattern || !idValid}
          onClick={submit}
        >
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
  const [err, setErr] = useState<string | null>(null);

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
      {err && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErr(null)}>
          {err}
        </Alert>
      )}
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Source</TableCell>
                <TableCell>Rule</TableCell>
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
                      <Chip
                        size="small"
                        label={humanizeEnum(src)}
                        color={sourceColor(src)}
                      />
                    </TableCell>
                    <TableCell>
                      {/* Rule ids are snake_case identifiers, not display names
                          (guards-profiles-7): humanize the id, show the human
                          description, and keep the pattern as monospace detail. */}
                      <Typography variant="body2">
                        {humanizeEnum(r.id) || r.id}
                      </Typography>
                      {r.description && (
                        <Typography variant="caption" color="text.secondary" display="block">
                          {r.description}
                        </Typography>
                      )}
                      {r.pattern && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ fontFamily: "monospace" }}
                        >
                          {r.pattern}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>{humanizeEnum(r.pattern_type)}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={humanizeEnum(r.outcome)}
                        color={guardOutcomeColor(r.outcome)}
                      />
                    </TableCell>
                    <TableCell>
                      <Switch
                        size="small"
                        checked={r.enabled !== false}
                        disabled={!editable}
                        onChange={async () => {
                          setErr(null);
                          try {
                            await apiSend(
                              "PUT",
                              `/dashboard/guards/rules/${r.id}`,
                              { enabled: r.enabled === false },
                            );
                            load();
                          } catch (e) {
                            setErr(errorMessage(e));
                          }
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
                              setErr(null);
                              try {
                                await apiSend(
                                  "DELETE",
                                  `/dashboard/guards/rules/${r.id}`,
                                );
                                load();
                              } catch (e) {
                                setErr(errorMessage(e));
                              }
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
    // GuardPolicy.preflight_check_strictness values are loose/medium/strict
    // (schemas/profile.py + profiles/presets.py) — NOT low/medium/high, which
    // silently stored an invalid value the engine ignored (guards-profiles-5).
    options: ["loose", "medium", "strict"],
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
          (e) => ({ list: [], error: errorMessage(e) }),
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
        setErr(errorMessage(e));
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
      setErr(errorMessage(e));
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
      setErr(errorMessage(e));
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
                  {humanizeEnum(o)}
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

/** Remaining time until an approval's hard-stop timeout, for the live badge. */
function timeoutRemaining(
  timeoutAt: string | null | undefined,
  nowMs: number,
): { text: string; expired: boolean } {
  if (!timeoutAt) return { text: "", expired: false };
  const end = new Date(timeoutAt).getTime();
  if (Number.isNaN(end)) return { text: "", expired: false };
  const diff = end - nowMs;
  if (diff <= 0) return { text: "expired", expired: true };
  const totalSec = Math.floor(diff / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return { text: `${m}:${String(s).padStart(2, "0")} left`, expired: false };
}

function ApprovalsTab() {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback((initial = false) => {
    if (initial) setLoading(true);
    apiGet<any>("/dashboard/guards/approvals/pending")
      .then((r) =>
        setApprovals(Array.isArray(r) ? r : (r.pending ?? r.items ?? [])),
      )
      .catch(() => setApprovals([]))
      .finally(() => setLoading(false));
  }, []);

  // Initial load + auto-refresh (gap-3-6): approvals hard-stop at their timeout
  // (ApprovalRouting.timeout_seconds, default 300s) and the backend self-heals
  // expired ids out of the pending SET, so poll to drop stale cards. A 1s tick
  // drives the live countdown badge.
  useEffect(() => {
    load(true);
    const refresh = window.setInterval(() => load(false), 15000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearInterval(refresh);
      window.clearInterval(tick);
    };
  }, [load]);

  const resolve = async (id: string, status: "approved" | "rejected") => {
    let reason: string | null = "";
    if (status === "rejected") {
      reason = window.prompt("Rejection reason");
      if (reason === null) return;
    }
    setError(null);
    setBusyId(id);
    try {
      // Operator identity (resolved_by) is stamped server-side from the
      // authenticated session — never sent from the browser (spoofable).
      await apiSend("PATCH", `/guards/approvals/${id}`, { status, reason });
      load(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <CircularProgress />;
  return (
    <Stack spacing={2}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {approvals.length === 0 && (
        <Typography color="text.secondary">No pending approvals.</Typography>
      )}
      {approvals.map((a) => {
        // Records are ApprovalRequest (schemas/guards.py): the action lives in
        // `action_summary`, not `action`/`summary`; there is no `session_key`
        // (only `session_id`) or `domain` (it's `decision_domain`). Reading the
        // wrong keys is why the card showed a raw UUID (gap-3-2).
        const id = String(a.id ?? a.request_id ?? "");
        const requested = formatRelativeTime(a.created_at ?? a.requested_at);
        const countdown = timeoutRemaining(a.timeout_at, now);
        const sessionLabel =
          a.session_key || (a.session_id ? shortId(a.session_id) : "");
        return (
          <Card key={id} variant="outlined">
            <CardContent>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                flexWrap="wrap"
                useFlexGap
              >
                <Typography variant="subtitle2">
                  {a.action_summary || a.explanation || `Approval ${shortId(id)}`}
                </Typography>
                {a.decision_domain && (
                  <Chip size="small" label={humanizeEnum(a.decision_domain)} />
                )}
                {countdown.text && (
                  <Chip
                    size="small"
                    variant="outlined"
                    color={countdown.expired ? "default" : "warning"}
                    label={countdown.text}
                  />
                )}
              </Stack>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                {sessionLabel ? `${sessionLabel} · ` : ""}requested{" "}
                <span title={requested.title}>{requested.text}</span>
              </Typography>
              {a.explanation && a.explanation !== a.action_summary && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mt: 0.5 }}
                >
                  {a.explanation}
                </Typography>
              )}
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                <Button
                  size="small"
                  variant="contained"
                  color="success"
                  disabled={busyId === id}
                  onClick={() => resolve(id, "approved")}
                >
                  Approve
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  disabled={busyId === id}
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
