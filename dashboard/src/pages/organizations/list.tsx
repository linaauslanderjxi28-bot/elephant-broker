// Organizations list page.
//
// Table of orgs with team / actor counts and a profile-override indicator.
// Create org requires authority >= 90; detail view requires >= 70.
// Rename (PUT /admin/organizations/{org_id}) gated at >= 90 (backend
// create_org authority rule).

import React, { useCallback, useEffect, useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import { apiGet, apiSend, useAuthority } from "../home/dashboardApi";

interface Org {
  org_id?: string;
  id?: string;
  eb_id?: string;
  name?: string;
  display_label?: string;
  team_count?: number;
  actor_count?: number;
  has_profile_override?: boolean;
}

function orgId(o: Org): string {
  return String(o.org_id ?? o.eb_id ?? o.id ?? "");
}

export const OrganizationsPage: React.FC = () => {
  const authority = useAuthority();
  const { push } = useNavigation();
  const [rows, setRows] = useState<Org[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editLabel, setEditLabel] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/dashboard/organizations");
      setRows(Array.isArray(res) ? res : (res.items ?? res.organizations ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    await apiSend("POST", "/admin/organizations", {
      name,
      display_label: label,
    });
    setCreateOpen(false);
    setName("");
    setLabel("");
    void load();
  };

  const saveEdit = async () => {
    if (!editId) return;
    setSaving(true);
    setEditError(null);
    try {
      await apiSend("PUT", `/admin/organizations/${editId}`, {
        name: editName,
        display_label: editLabel,
      });
      setEditId(null);
      void load();
    } catch (e) {
      setEditError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Organizations</Typography>
        {authority >= 90 && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            Create Organization
          </Button>
        )}
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? null : (
        <Paper variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Label</TableCell>
                <TableCell align="right">Teams</TableCell>
                <TableCell align="right">Actors</TableCell>
                <TableCell>Profile</TableCell>
                {authority >= 90 && <TableCell padding="checkbox" />}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((o) => (
                <TableRow
                  key={orgId(o)}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() => push(`/organizations/${orgId(o)}`)}
                >
                  <TableCell>{o.name}</TableCell>
                  <TableCell>{o.display_label}</TableCell>
                  <TableCell align="right">{o.team_count ?? 0}</TableCell>
                  <TableCell align="right">{o.actor_count ?? 0}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={o.has_profile_override ? "custom" : "default"}
                      color={o.has_profile_override ? "secondary" : "default"}
                    />
                  </TableCell>
                  {authority >= 90 && (
                    <TableCell padding="checkbox">
                      <IconButton
                        size="small"
                        aria-label="Edit organization"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditId(orgId(o));
                          setEditName(String(o.name ?? ""));
                          setEditLabel(String(o.display_label ?? ""));
                          setEditError(null);
                        }}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={authority >= 90 ? 6 : 5}>
                    No organizations.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} fullWidth>
        <DialogTitle>Create organization</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <TextField
              label="Display label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!name} onClick={create}>
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={editId !== null}
        onClose={() => setEditId(null)}
        fullWidth
      >
        <DialogTitle>Edit organization</DialogTitle>
        <DialogContent>
          {editError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {editError}
            </Alert>
          )}
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              autoFocus
              label="Name"
              required
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
            />
            <TextField
              label="Display label"
              value={editLabel}
              onChange={(e) => setEditLabel(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditId(null)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!editName.trim() || saving}
            onClick={saveEdit}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OrganizationsPage;
