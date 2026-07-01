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

function GoalRow(props: {
  goal: GoalState;
  depth: number;
  onView: (id: string) => void;
}) {
  const { goal, depth } = props;
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<GoalState[] | null>(null);
  const [loadingChildren, setLoadingChildren] = useState(false);
  const authority = useAuthority();

  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      setLoadingChildren(true);
      try {
        const res = await apiGet<any>("/goals/hierarchy", {
          root_goal_id: goalId(goal),
        });
        const kids: GoalState[] = Array.isArray(res)
          ? res
          : (res.items ?? res.children ?? res.goals ?? []);
        setChildren(kids.filter((k) => goalId(k) !== goalId(goal)));
      } catch {
        setChildren([]);
      } finally {
        setLoadingChildren(false);
      }
    }
  }, [open, children, goal]);

  return (
    <>
      <ListItem
        sx={{ pl: 2 + depth * 3 }}
        secondaryAction={
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              label={goal.status || "active"}
              color={goalStatusColor(goal.status)}
            />
            {goal.scope && <Chip size="small" label={goal.scope} variant="outlined" />}
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
      <Collapse in={open} timeout="auto" unmountOnExit>
        <Box sx={{ pl: 4 + depth * 3, pr: 2, pb: 1 }}>
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
          {goal.blockers && goal.blockers.length > 0 && (
            <Alert severity="warning" sx={{ my: 1 }}>
              Blockers: {goal.blockers.join("; ")}
            </Alert>
          )}
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            {authority >= 50 && (
              <Button
                size="small"
                onClick={async () => {
                  const text = window.prompt("Blocker description");
                  if (text)
                    await apiSend(
                      "POST",
                      `/admin/goals/${goalId(goal)}/blocker`,
                      { blocker: text },
                    );
                }}
              >
                Add blocker
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
                />
              ))}
            </List>
          )}
        </Box>
      </Collapse>
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
