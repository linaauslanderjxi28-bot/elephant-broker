// Authority Rules settings page (authority >= 90).
//
// Views all authority rules with effective values and edits / resets overrides
// via GET/PUT/DELETE /admin/authority-rules.

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

interface Rule {
  action: string;
  min_level?: number;
  required_level?: number;
  require_matching_org?: boolean;
  require_matching_team?: boolean;
  require_self?: boolean;
  override_exempt_level?: number;
  source?: string;
}

export const AuthorityRulesPage: React.FC = () => {
  const authority = useAuthority();
  const [rows, setRows] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Rule | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/admin/authority-rules");
      setRows(Array.isArray(res) ? res : (res.rules ?? res.items ?? []));
    } catch (e) {
      setError((e as Error).message);
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

  const level = (r: Rule) => r.required_level ?? r.min_level ?? 0;

  const save = async () => {
    if (!editing) return;
    await apiSend("PUT", `/admin/authority-rules/${editing.action}`, editing);
    setEditing(null);
    void load();
  };

  const reset = async (action: string) => {
    if (!window.confirm("Reset to default?")) return;
    await apiSend("DELETE", `/admin/authority-rules/${action}`);
    void load();
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
                <TableCell>Source</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.action}>
                  <TableCell>{ACTION_LABELS[r.action] ?? r.action}</TableCell>
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
                      {r.require_self && (
                        <Chip size="small" label="self only" />
                      )}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={r.source ?? "default"}
                      color={r.source === "custom" ? "secondary" : "default"}
                    />
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" onClick={() => setEditing({ ...r })}>
                      Edit
                    </Button>
                    <Button size="small" onClick={() => reset(r.action)}>
                      Reset
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>No rules.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Dialog open={!!editing} onClose={() => setEditing(null)} fullWidth>
        <DialogTitle>
          Edit rule: {editing && (ACTION_LABELS[editing.action] ?? editing.action)}
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
                    required_level: Number(e.target.value),
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
                  checked={!!editing.require_self}
                  onChange={(e) =>
                    setEditing({ ...editing, require_self: e.target.checked })
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
    </Box>
  );
};

export default AuthorityRulesPage;
