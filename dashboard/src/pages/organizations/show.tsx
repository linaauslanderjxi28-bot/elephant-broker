// Organization detail page.
//
// Org info, teams with expandable member lists (add / remove members), and a
// form-based profile-override editor. Team management gated at authority >= 50;
// override editing at >= 70. Org rename gated at >= 90 (backend create_org
// rule); team rename at >= 70 (backend create_team rule).

import React, { useCallback, useEffect, useState } from "react";
import { useParsed } from "@refinedev/core";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  IconButton,
  List,
  ListItem,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import { apiGet, apiSend, useAuthority } from "../home/dashboardApi";

interface OrgDetail {
  org_id?: string;
  eb_id?: string;
  name?: string;
  display_label?: string;
  teams?: any[];
  actors?: any[];
  profile_overrides?: Record<string, any>;
}

function TeamPanel(props: {
  team: any;
  orgId: string;
  onChanged: () => void;
  canManage: boolean;
  canEdit: boolean;
}) {
  const { team } = props;
  const teamId = String(team.team_id ?? team.eb_id ?? team.id ?? "");
  const [members, setMembers] = useState<any[] | null>(null);
  const [newActor, setNewActor] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editLabel, setEditLabel] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const saveEdit = async () => {
    setSaving(true);
    setEditError(null);
    try {
      await apiSend("PUT", `/admin/teams/${teamId}`, {
        name: editName,
        display_label: editLabel,
        org_id: props.orgId,
      });
      setEditOpen(false);
      props.onChanged();
    } catch (e) {
      setEditError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const loadMembers = useCallback(async () => {
    try {
      const res = await apiGet<any>(`/admin/teams/${teamId}/members`);
      setMembers(Array.isArray(res) ? res : (res.items ?? res.members ?? []));
    } catch {
      setMembers([]);
    }
  }, [teamId]);

  return (
    <>
    <Accordion
      onChange={(_, exp) => {
        if (exp && members === null) void loadMembers();
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography sx={{ flex: 1 }}>
          {team.name ?? team.display_label}
        </Typography>
        {props.canEdit && (
          <IconButton
            component="span"
            size="small"
            aria-label="Edit team"
            sx={{ mr: 1, my: -0.5 }}
            onClick={(e) => {
              e.stopPropagation();
              setEditName(String(team.name ?? ""));
              setEditLabel(String(team.display_label ?? ""));
              setEditError(null);
              setEditOpen(true);
            }}
          >
            <EditIcon fontSize="small" />
          </IconButton>
        )}
        <Typography variant="body2" color="text.secondary">
          {team.member_count ?? members?.length ?? 0} members
        </Typography>
      </AccordionSummary>
      <AccordionDetails>
        <List dense>
          {(members ?? []).map((m) => {
            const mid = String(m.actor_id ?? m.eb_id ?? m.id ?? "");
            return (
              <ListItem
                key={mid}
                secondaryAction={
                  props.canManage && (
                    <IconButton
                      edge="end"
                      onClick={async () => {
                        if (!window.confirm("Remove member?")) return;
                        await apiSend(
                          "DELETE",
                          `/admin/teams/${teamId}/members/${mid}`,
                        );
                        void loadMembers();
                        props.onChanged();
                      }}
                    >
                      <DeleteIcon />
                    </IconButton>
                  )
                }
              >
                <ListItemText
                  primary={m.display_name ?? mid}
                  secondary={m.actor_type ?? m.type}
                />
              </ListItem>
            );
          })}
          {members !== null && members.length === 0 && (
            <ListItemText primary="No members." sx={{ px: 2 }} />
          )}
        </List>
        {props.canManage && (
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <TextField
              size="small"
              label="Actor ID"
              value={newActor}
              onChange={(e) => setNewActor(e.target.value)}
            />
            <Button
              onClick={async () => {
                if (!newActor) return;
                await apiSend("POST", `/admin/teams/${teamId}/members`, {
                  actor_id: newActor,
                });
                setNewActor("");
                void loadMembers();
                props.onChanged();
              }}
            >
              Add member
            </Button>
          </Stack>
        )}
      </AccordionDetails>
    </Accordion>

    <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth>
        <DialogTitle>Edit team</DialogTitle>
        <DialogContent>
          {editError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {editError}
            </Alert>
          )}
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              autoFocus
              label="Team name"
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
          <Button onClick={() => setEditOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!editName.trim() || saving}
            onClick={saveEdit}
          >
            Save
          </Button>
        </DialogActions>
    </Dialog>
    </>
  );
}

export const OrganizationShowPage: React.FC = () => {
  const { id } = useParsed();
  const orgId = String(id ?? "");
  const authority = useAuthority();
  const [data, setData] = useState<OrgDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addTeamOpen, setAddTeamOpen] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [editOrgOpen, setEditOrgOpen] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [orgLabel, setOrgLabel] = useState("");
  const [orgEditError, setOrgEditError] = useState<string | null>(null);
  const [orgSaving, setOrgSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // There is no GET /dashboard/organizations/{org_id} endpoint on the
      // backend — compose the detail view from the org list plus the admin
      // endpoints. Teams / actors / overrides are best-effort: they require
      // higher authority (403s degrade to empty sections, not a dead page).
      const res = await apiGet<{ organizations?: any[] }>(
        "/dashboard/organizations",
      );
      const org = (res.organizations ?? []).find(
        (o) => String(o.org_id ?? o.eb_id ?? "") === orgId,
      );
      if (!org) throw new Error(`Organization ${orgId} not found`);
      const [teams, actors, overrides] = await Promise.all([
        apiGet<any[]>("/admin/teams", { org_id: orgId }).catch(() => []),
        apiGet<any[]>("/admin/actors", { org_id: orgId }).catch(() => []),
        apiGet<any[]>(`/admin/profiles/overrides/${orgId}`).catch(
          () => [] as any[],
        ),
      ]);
      const profile_overrides: Record<string, any> = {};
      for (const ov of Array.isArray(overrides) ? overrides : []) {
        if (ov && ov.profile_id) profile_overrides[ov.profile_id] = ov.overrides;
      }
      setData({
        org_id: orgId,
        name: org.name,
        display_label: org.display_label,
        teams: Array.isArray(teams) ? teams : [],
        actors: Array.isArray(actors) ? actors : [],
        profile_overrides,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    if (orgId) void load();
  }, [orgId, load]);

  if (loading) return <CircularProgress sx={{ m: 4 }} />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;

  const saveOrgEdit = async () => {
    setOrgSaving(true);
    setOrgEditError(null);
    try {
      await apiSend("PUT", `/admin/organizations/${orgId}`, {
        name: orgName,
        display_label: orgLabel,
      });
      setEditOrgOpen(false);
      void load();
    } catch (e) {
      setOrgEditError((e as Error).message);
    } finally {
      setOrgSaving(false);
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ mb: 1.5 }}
      >
        <Typography variant="h5">
          {data.name}
          {data.display_label ? ` — ${data.display_label}` : ""}
        </Typography>
        {authority >= 90 && (
          <IconButton
            size="small"
            aria-label="Edit organization"
            onClick={() => {
              setOrgName(String(data.name ?? ""));
              setOrgLabel(String(data.display_label ?? ""));
              setOrgEditError(null);
              setEditOrgOpen(true);
            }}
          >
            <EditIcon fontSize="small" />
          </IconButton>
        )}
      </Stack>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", md: "7fr 5fr" },
        }}
      >
        <Card variant="outlined">
            <CardContent>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
              >
                <Typography variant="subtitle2">Teams</Typography>
                {authority >= 70 && (
                  <Button size="small" onClick={() => setAddTeamOpen(true)}>
                    + Add Team
                  </Button>
                )}
              </Stack>
              {(data.teams ?? []).map((t, i) => (
                <TeamPanel
                  key={i}
                  team={t}
                  orgId={orgId}
                  onChanged={load}
                  canManage={authority >= 50}
                  canEdit={authority >= 70}
                />
              ))}
              {(data.teams ?? []).length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No teams.
                </Typography>
              )}
            </CardContent>
          </Card>

        <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle2" gutterBottom>
                Actors
              </Typography>
              <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                {(data.actors ?? []).map((a, i) => (
                  <Chip
                    key={i}
                    size="small"
                    label={a.display_name ?? a.actor_id}
                  />
                ))}
                {(data.actors ?? []).length === 0 && (
                  <Typography variant="body2" color="text.secondary">
                    None
                  </Typography>
                )}
              </Stack>
            </CardContent>
          </Card>
      </Box>

      <Accordion sx={{ mt: 2 }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Profile overrides</Typography>
        </AccordionSummary>
        <AccordionDetails>
          {data.profile_overrides &&
          Object.keys(data.profile_overrides).length > 0 ? (
            Object.entries(data.profile_overrides).map(([k, v]) => (
              <Stack
                key={k}
                direction="row"
                justifyContent="space-between"
                sx={{ py: 0.5 }}
              >
                <Typography variant="body2">{k}</Typography>
                <Typography variant="body2">{JSON.stringify(v)}</Typography>
              </Stack>
            ))
          ) : (
            <Typography variant="body2" color="text.secondary">
              No overrides — using default profile. Edit overrides requires
              authority &ge; 70 (form editor loads the resolved profile).
            </Typography>
          )}
        </AccordionDetails>
      </Accordion>

      <Dialog open={addTeamOpen} onClose={() => setAddTeamOpen(false)} fullWidth>
        <DialogTitle>Add team</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Team name"
            sx={{ mt: 1 }}
            value={teamName}
            onChange={(e) => setTeamName(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddTeamOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!teamName}
            onClick={async () => {
              await apiSend("POST", "/admin/teams", {
                name: teamName,
                org_id: orgId,
              });
              setTeamName("");
              setAddTeamOpen(false);
              void load();
            }}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={editOrgOpen}
        onClose={() => setEditOrgOpen(false)}
        fullWidth
      >
        <DialogTitle>Edit organization</DialogTitle>
        <DialogContent>
          {orgEditError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {orgEditError}
            </Alert>
          )}
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              autoFocus
              label="Name"
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
            />
            <TextField
              label="Display label"
              value={orgLabel}
              onChange={(e) => setOrgLabel(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditOrgOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!orgName.trim() || orgSaving}
            onClick={saveOrgEdit}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OrganizationShowPage;
