// Authority Rules settings page (authority >= 90).
//
// Views all authority rules with effective values and edits / resets overrides
// via GET/PUT/DELETE /admin/authority-rules.
//
// GET /admin/authority-rules returns a DICT keyed by action
// ({ create_org: { min_authority_level, require_matching_org, ... }, ... }), not
// a list — the previous array/`.rules` handling always yielded "No rules."
// (settings-1). We normalize the dict into rows and map the backend field names
// (min_authority_level / require_self_ownership / matching_exempt_level).

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import {
  apiGet,
  apiSend,
  authorityLabel,
  AUTHORITY_OPTIONS,
  useAuthority,
} from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";

const ACTION_LABELS: Record<string, string> = {
  create_global_goal: "Create global goal",
  create_org_goal: "Create organization goal",
  create_team_goal: "Create team goal",
  create_actor_goal: "Create actor goal",
  create_org: "Create organization",
  create_team: "Create team",
  add_team_member: "Add team member",
  remove_team_member: "Remove team member",
  register_actor: "Register actor",
  register_org_profile_override: "Edit profile overrides",
  merge_actors: "Merge actor identities",
};

/** A rule as returned by the backend authority store (field names verified). */
interface Rule {
  action: string;
  min_authority_level?: number;
  require_matching_org?: boolean;
  require_matching_team?: boolean;
  require_self_ownership?: boolean;
  matching_exempt_level?: number | null;
}

/**
 * Normalize the `/admin/authority-rules` payload into rows. The backend returns
 * a plain `{ action: rule }` dict; we also tolerate a bare array or an
 * `{ rules | items }` envelope for forward-compatibility.
 */
function toRows(payload: unknown): Rule[] {
  if (Array.isArray(payload)) return payload as Rule[];
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    const envelope = obj.rules ?? obj.items;
    if (Array.isArray(envelope)) return envelope as Rule[];
    return Object.entries(obj).map(([action, rule]) => ({
      action,
      ...(rule && typeof rule === "object" ? (rule as Record<string, unknown>) : {}),
    })) as Rule[];
  }
  return [];
}

export const AuthorityRulesPage: React.FC = () => {
  const authority = useAuthority();
  const [rows, setRows] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [resetTarget, setResetTarget] = useState<Rule | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<unknown>("/admin/authority-rules");
      setRows(toRows(res));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (authority < 90) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="warning">
          Authority rules require authority &ge; 90.
        </Alert>
      </Box>
    );
  }

  const level = (r: Rule) => r.min_authority_level ?? 0;

  const actionTitle = (action: string) => ACTION_LABELS[action] ?? action;

  const save = async () => {
    if (!editing) return;
    setError(null);
    try {
      // Send exactly the UpdateAuthorityRuleRequest shape.
      await apiSend("PUT", `/admin/authority-rules/${editing.action}`, {
        min_authority_level: level(editing),
        require_matching_org: !!editing.require_matching_org,
        require_matching_team: !!editing.require_matching_team,
        require_self_ownership: !!editing.require_self_ownership,
        matching_exempt_level: editing.matching_exempt_level ?? null,
      });
      setEditing(null);
      void load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const confirmReset = async () => {
    if (!resetTarget) return;
    const action = resetTarget.action;
    setResetTarget(null);
    setError(null);
    try {
      await apiSend("DELETE", `/admin/authority-rules/${action}`);
      void load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Authority Rules
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Action</TableCell>
                <TableCell>Required level</TableCell>
                <TableCell>Matching rules</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.action}>
                  <TableCell>{actionTitle(r.action)}</TableCell>
                  <TableCell>
                    {authorityLabel(level(r))} ({level(r)})
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5}>
                      {r.require_matching_org && (
                        <Chip size="small" label="match org" />
                      )}
                      {r.require_matching_team && (
                        <Chip size="small" label="match team" />
                      )}
                      {r.require_self_ownership && (
                        <Chip size="small" label="self only" />
                      )}
                      {!r.require_matching_org &&
                        !r.require_matching_team &&
                        !r.require_self_ownership && (
                          <Typography variant="body2" color="text.disabled">
                            —
                          </Typography>
                        )}
                    </Stack>
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" onClick={() => setEditing({ ...r })}>
                      Edit
                    </Button>
                    <Button size="small" onClick={() => setResetTarget(r)}>
                      Reset
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4}>No rules.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Dialog open={!!editing} onClose={() => setEditing(null)} fullWidth>
        <DialogTitle>
          Edit rule: {editing && actionTitle(editing.action)}
        </DialogTitle>
        <DialogContent>
          {editing && (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <TextField
                select
                label="Required level"
                value={level(editing)}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    min_authority_level: Number(e.target.value),
                  })
                }
              >
                {AUTHORITY_OPTIONS.map((o) => (
                  <MenuItem key={o.value} value={o.value}>
                    {o.label}
                  </MenuItem>
                ))}
              </TextField>
              <Stack direction="row" alignItems="center" justifyContent="space-between">
                <Typography variant="body2">Require matching org</Typography>
                <Switch
                  checked={!!editing.require_matching_org}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      require_matching_org: e.target.checked,
                    })
                  }
                />
              </Stack>
              <Stack direction="row" alignItems="center" justifyContent="space-between">
                <Typography variant="body2">Require matching team</Typography>
                <Switch
                  checked={!!editing.require_matching_team}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      require_matching_team: e.target.checked,
                    })
                  }
                />
              </Stack>
              <Stack direction="row" alignItems="center" justifyContent="space-between">
                <Typography variant="body2">Require self ownership</Typography>
                <Switch
                  checked={!!editing.require_self_ownership}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      require_self_ownership: e.target.checked,
                    })
                  }
                />
              </Stack>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button variant="contained" onClick={save}>
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!resetTarget} onClose={() => setResetTarget(null)}>
        <DialogTitle>Reset authority rule?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Reset{" "}
            <strong>{resetTarget && actionTitle(resetTarget.action)}</strong> to
            its shipped default? Any custom override for this action will be
            removed.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setResetTarget(null)}>Cancel</Button>
          <Button color="warning" variant="contained" onClick={confirmReset}>
            Reset to default
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default AuthorityRulesPage;
