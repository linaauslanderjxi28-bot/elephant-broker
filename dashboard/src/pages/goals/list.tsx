// Goals page.
//
// Two tabs: Persistent goals (Neo4j, durable, hierarchical, creatable) and
// Session goals (Redis, ephemeral, monitored per active session). Persistent
// goals load root nodes and lazy-load children through the hierarchy endpoint.

import React, { useCallback, useEffect, useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import {
  apiGet,
  apiSend,
  goalStatusColor,
  relativeTime,
  scopesForAuthority,
  useAuthority,
} from "../home/dashboardApi";

interface GoalState {
  goal_id?: string;
  id?: string;
  eb_id?: string;
  title: string;
  description?: string;
  status?: string;
  scope?: string;
  parent_goal_id?: string | null;
  success_criteria?: string[];
  blockers?: string[];
  owner_actor_ids?: string[];
  confidence?: number;
  created_at?: string;
}

function goalId(g: GoalState): string {
  return String(g.goal_id ?? g.eb_id ?? g.id ?? "");
}

// Allowed status transitions per current GoalStatus (schemas/goal.py enum:
// proposed | active | paused | completed | abandoned). The backend
// (PUT /admin/goals/{id} -> GoalManager.update_goal_status) accepts any enum
// value; this map just presents sensible lifecycle actions.
const STATUS_ACTIONS: Record<string, Array<{ label: string; status: string }>> = {
  proposed: [
    { label: "Activate", status: "active" },
    { label: "Abandon", status: "abandoned" },
  ],
  active: [
    { label: "Complete", status: "completed" },
    { label: "Pause", status: "paused" },
    { label: "Abandon", status: "abandoned" },
  ],
  paused: [
    { label: "Reactivate", status: "active" },
    { label: "Complete", status: "completed" },
    { label: "Abandon", status: "abandoned" },
  ],
  completed: [{ label: "Reactivate", status: "active" }],
  abandoned: [{ label: "Reactivate", status: "active" }],
};

// Minimum authority to create a subgoal, mirroring the backend's
// SCOPE_ACTION_MAP -> authority_store defaults for the parent's scope
// (global: create_global_goal 90, organization: 70, team: 50, actor: 0
// with self-ownership enforced server-side).
function subgoalMinAuthority(scope: string | undefined): number {
  switch ((scope || "").toLowerCase()) {
    case "global":
      return 90;
    case "organization":
      return 70;
    case "team":
      return 50;
    default:
      return 0;
  }
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function AddSubgoalDialog(props: {
  open: boolean;
  parent: GoalState;
  onClose: () => void;
  onCreated: (created: GoalState) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState("");
  const [owners, setOwners] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    const ownerIds = owners
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const bad = ownerIds.find((o) => !UUID_RE.test(o));
    if (bad) {
      setErr(`Owner actor id is not a valid UUID: ${bad}`);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const created = await apiSend<GoalState>(
        "POST",
        `/admin/goals/${goalId(props.parent)}/subgoal`,
        {
          title,
          description,
          success_criteria: criteria
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
          owner_actor_ids: ownerIds,
        },
      );
      props.onCreated(created);
      props.onClose();
      setTitle("");
      setDescription("");
      setCriteria("");
      setOwners("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Add subgoal to “{props.parent.title}”</DialogTitle>
      <DialogContent>
        {err && <Alert severity="error">{err}</Alert>}
        <Typography variant="caption" color="text.secondary">
          The subgoal inherits the parent&apos;s scope
          {props.parent.scope ? ` (${props.parent.scope})` : ""}, org and team.
        </Typography>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Title"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <TextField
            label="Success criteria (one per line)"
            multiline
            minRows={2}
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
          />
          <TextField
            label="Owner actor IDs (comma-separated UUIDs, optional)"
            value={owners}
            onChange={(e) => setOwners(e.target.value)}
            helperText="Owners can only be set at creation — the backend has no owner-reassignment endpoint."
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button variant="contained" disabled={!title || saving} onClick={submit}>
          Create subgoal
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function AddBlockerDialog(props: {
  open: boolean;
  goal: GoalState;
  onClose: () => void;
  onAdded: (updated: GoalState) => void;
}) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      // POST /admin/goals/{id}/blocker returns the full updated GoalState.
      const updated = await apiSend<GoalState>(
        "POST",
        `/admin/goals/${goalId(props.goal)}/blocker`,
        { blocker: text.trim() },
      );
      props.onAdded(updated);
      props.onClose();
      setText("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Add blocker to “{props.goal.title}”</DialogTitle>
      <DialogContent>
        {err && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {err}
          </Alert>
        )}
        <TextField
          label="Blocker description"
          required
          fullWidth
          multiline
          minRows={2}
          sx={{ mt: 1 }}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Typography variant="caption" color="text.secondary">
          Blockers can only be appended — the backend exposes no
          blocker-removal endpoint.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!text.trim() || saving}
          onClick={submit}
        >
          Add blocker
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function GoalRow(props: {
  goal: GoalState;
  depth: number;
  onView: (id: string) => void;
  onChanged: () => void;
}) {
  const { goal, depth } = props;
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<GoalState[] | null>(null);
  const [loadingChildren, setLoadingChildren] = useState(false);
  const [status, setStatus] = useState(goal.status);
  const [blockers, setBlockers] = useState<string[]>(goal.blockers ?? []);
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [subgoalOpen, setSubgoalOpen] = useState(false);
  const [blockerOpen, setBlockerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rowErr, setRowErr] = useState<string | null>(null);
  const authority = useAuthority();

  // GET /goals/hierarchy returns a GoalHierarchy model
  // ({ root_goals: GoalState[], children: { "<root_id>": GoalState[] } },
  // schemas/goal.py) — this goal's children live in the children map under
  // this goal's own id.
  const loadChildren = useCallback(async () => {
    setLoadingChildren(true);
    try {
      const id = goalId(goal);
      const res = await apiGet<any>("/goals/hierarchy", { root_goal_id: id });
      const kids: GoalState[] = Array.isArray(res)
        ? res
        : (res.children?.[id] ?? res.items ?? res.goals ?? []);
      setChildren(kids.filter((k) => goalId(k) !== id));
    } catch {
      setChildren([]);
    } finally {
      setLoadingChildren(false);
    }
  }, [goal]);

  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      await loadChildren();
    }
  }, [open, children, loadChildren]);

  // PUT /admin/goals/{id} — the backend only applies the "status" field
  // (admin.py:update_persistent_goal); other fields in the body are ignored,
  // so there is no owner-reassignment support here.
  const changeStatus = useCallback(
    async (next: string) => {
      setMenuAnchor(null);
      setBusy(true);
      setRowErr(null);
      try {
        await apiSend("PUT", `/admin/goals/${goalId(goal)}`, { status: next });
        setStatus(next);
        // Note: GET /admin/goals only returns active goals, so a root moved
        // away from "active" disappears from the list on refresh.
        props.onChanged();
      } catch (e) {
        setRowErr((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [goal, props],
  );

  const statusActions = STATUS_ACTIONS[(status || "active").toLowerCase()] ?? [];

  return (
    <>
      <ListItem
        sx={{ pl: 2 + depth * 3 }}
        secondaryAction={
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              label={status || "active"}
              color={goalStatusColor(status)}
            />
            {goal.scope && <Chip size="small" label={goal.scope} variant="outlined" />}
            {authority >= 70 && statusActions.length > 0 && (
              <IconButton
                size="small"
                aria-label="Goal status actions"
                disabled={busy}
                onClick={(e) => setMenuAnchor(e.currentTarget)}
              >
                <MoreVertIcon fontSize="small" />
              </IconButton>
            )}
          </Stack>
        }
      >
        <IconButton size="small" onClick={toggle}>
          {open ? <ExpandMoreIcon /> : <ChevronRightIcon />}
        </IconButton>
        <ListItemText
          primary={goal.title}
          secondary={goal.description?.slice(0, 100)}
        />
      </ListItem>
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => setMenuAnchor(null)}
      >
        {statusActions.map((a) => (
          <MenuItem key={a.status} onClick={() => void changeStatus(a.status)}>
            {a.label}
          </MenuItem>
        ))}
      </Menu>
      <Collapse in={open} timeout="auto" unmountOnExit>
        <Box sx={{ pl: 4 + depth * 3, pr: 2, pb: 1 }}>
          {rowErr && (
            <Alert severity="error" onClose={() => setRowErr(null)} sx={{ my: 1 }}>
              {rowErr}
            </Alert>
          )}
          {goal.success_criteria && goal.success_criteria.length > 0 && (
            <>
              <Typography variant="caption" color="text.secondary">
                Success criteria
              </Typography>
              <ul style={{ marginTop: 2 }}>
                {goal.success_criteria.map((c, i) => (
                  <li key={i}>
                    <Typography variant="body2">{c}</Typography>
                  </li>
                ))}
              </ul>
            </>
          )}
          {blockers.length > 0 && (
            // Blocker removal is not built: the backend only exposes
            // POST /admin/goals/{id}/blocker (append). No removal endpoint
            // exists in api/routes/admin.py or api/routes/goals.py.
            <Alert severity="warning" sx={{ my: 1 }}>
              Blockers: {blockers.join("; ")}
              <Typography variant="caption" display="block">
                Blocker removal is not supported by the backend (append-only).
              </Typography>
            </Alert>
          )}
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            {/* Backend gates blocker-add at create_global_goal (authority 90). */}
            {authority >= 90 && (
              <Button
                size="small"
                disabled={busy}
                onClick={() => setBlockerOpen(true)}
              >
                Add blocker
              </Button>
            )}
            {authority >= subgoalMinAuthority(goal.scope) && (
              <Button size="small" onClick={() => setSubgoalOpen(true)}>
                Add subgoal
              </Button>
            )}
            <Button
              size="small"
              onClick={() =>
                props.onView(`/memory?goal_id=${goalId(goal)}`)
              }
            >
              Related facts
            </Button>
          </Stack>
          {loadingChildren && <CircularProgress size={18} sx={{ mt: 1 }} />}
          {children && children.length > 0 && (
            <List dense disablePadding>
              {children.map((c) => (
                <GoalRow
                  key={goalId(c)}
                  goal={c}
                  depth={depth + 1}
                  onView={props.onView}
                  onChanged={props.onChanged}
                />
              ))}
            </List>
          )}
        </Box>
      </Collapse>
      <AddSubgoalDialog
        open={subgoalOpen}
        parent={goal}
        onClose={() => setSubgoalOpen(false)}
        onCreated={(created) => {
          // If children were never fetched, refetch the hierarchy so existing
          // siblings are not masked by seeding the list with just the new one.
          if (children === null) {
            void loadChildren();
          } else {
            setChildren([...children, created]);
          }
          setOpen(true);
        }}
      />
      <AddBlockerDialog
        open={blockerOpen}
        goal={goal}
        onClose={() => setBlockerOpen(false)}
        onAdded={(updated) => setBlockers(updated.blockers ?? [])}
      />
    </>
  );
}

function CreateGoalDialog(props: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const authority = useAuthority();
  const scopes = scopesForAuthority(authority);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState(scopes[0] || "ACTOR");
  const [criteria, setCriteria] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await apiSend("POST", "/admin/goals", {
        title,
        description,
        scope,
        success_criteria: criteria
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      props.onCreated();
      props.onClose();
      setTitle("");
      setDescription("");
      setCriteria("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Create persistent goal</DialogTitle>
      <DialogContent>
        {err && <Alert severity="error">{err}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Title"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <TextField
            select
            label="Scope"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            {scopes.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Success criteria (one per line)"
            multiline
            minRows={2}
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!title || saving}
          onClick={submit}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export const GoalsPage: React.FC = () => {
  const [tab, setTab] = useState(0);
  const { push } = useNavigation();
  const authority = useAuthority();

  // Persistent
  const [roots, setRoots] = useState<GoalState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  // Session
  const [sessions, setSessions] = useState<string[]>([]);
  const [selectedSession, setSelectedSession] = useState("");
  const [sessionGoals, setSessionGoals] = useState<GoalState[]>([]);

  const loadRoots = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/admin/goals");
      const items: GoalState[] = Array.isArray(res)
        ? res
        : (res.items ?? res.goals ?? []);
      setRoots(items.filter((g) => !g.parent_goal_id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRoots();
  }, [loadRoots]);

  useEffect(() => {
    if (tab !== 1) return;
    void (async () => {
      try {
        const res = await apiGet<any>("/dashboard/sessions/active");
        setSessions(res.sessions ?? res.items ?? []);
      } catch {
        setSessions([]);
      }
    })();
  }, [tab]);

  useEffect(() => {
    if (!selectedSession) return;
    void (async () => {
      try {
        const res = await apiGet<any>("/goals/session", {
          session_key: selectedSession,
        });
        setSessionGoals(Array.isArray(res) ? res : (res.items ?? res.goals ?? []));
      } catch {
        setSessionGoals([]);
      }
    })();
  }, [selectedSession]);

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1 }}
      >
        <Typography variant="h5">Goals</Typography>
        {tab === 0 && authority >= 50 && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            Create Goal
          </Button>
        )}
      </Stack>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Persistent" />
        <Tab label="Session" />
      </Tabs>

      {tab === 0 && (
        <>
          {error && <Alert severity="error">{error}</Alert>}
          {loading ? (
            <CircularProgress />
          ) : roots.length === 0 ? (
            <Typography color="text.secondary">
              No persistent goals.
            </Typography>
          ) : (
            <List>
              {roots.map((g) => (
                <GoalRow
                  key={goalId(g)}
                  goal={g}
                  depth={0}
                  onView={(path) => push(path)}
                  onChanged={loadRoots}
                />
              ))}
            </List>
          )}
        </>
      )}

      {tab === 1 && (
        <>
          <TextField
            select
            label="Active session"
            value={selectedSession}
            onChange={(e) => setSelectedSession(e.target.value)}
            sx={{ minWidth: 320, mb: 2 }}
          >
            {sessions.length === 0 && (
              <MenuItem value="" disabled>
                No active sessions
              </MenuItem>
            )}
            {sessions.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </TextField>
          {sessionGoals.length === 0 ? (
            <Typography color="text.secondary">
              {selectedSession
                ? "No goals for this session."
                : "Select a session."}
            </Typography>
          ) : (
            <List>
              {sessionGoals.map((g) => (
                <ListItem
                  key={goalId(g)}
                  secondaryAction={
                    <Stack direction="row" spacing={1}>
                      <Chip
                        size="small"
                        label={goal_blockers(g)}
                        variant="outlined"
                      />
                      <Chip
                        size="small"
                        label={g.status || "active"}
                        color={goalStatusColor(g.status)}
                      />
                    </Stack>
                  }
                >
                  <ListItemText
                    primary={g.title}
                    secondary={relativeTime(g.created_at)}
                  />
                </ListItem>
              ))}
            </List>
          )}
        </>
      )}

      <CreateGoalDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={loadRoots}
      />
    </Box>
  );
};

function goal_blockers(g: GoalState): string {
  const n = g.blockers?.length ?? 0;
  return `${n} blocker${n === 1 ? "" : "s"}`;
}

export default GoalsPage;
