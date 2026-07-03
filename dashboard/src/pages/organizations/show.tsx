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
import { humanizeEnum } from "../../lib/format";
import { actorDisplayName } from "../../lib/labels";
import { errorMessage } from "../../lib/errors";

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
  const [memberError, setMemberError] = useState<string | null>(null);
  const [memberBusy, setMemberBusy] = useState(false);

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
      setEditError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const removeMember = async (mid: string) => {
    if (!window.confirm("Remove member?")) return;
    setMemberError(null);
    setMemberBusy(true);
    try {
      await apiSend("DELETE", `/admin/teams/${teamId}/members/${mid}`);
      await loadMembers();
      props.onChanged();
    } catch (e) {
      setMemberError(errorMessage(e));
    } finally {
      setMemberBusy(false);
    }
  };

  const addMember = async () => {
    if (!newActor.trim()) return;
    setMemberError(null);
    setMemberBusy(true);
    try {
      await apiSend("POST", `/admin/teams/${teamId}/members`, {
        actor_id: newActor.trim(),
      });
      setNewActor("");
      await loadMembers();
      props.onChanged();
    } catch (e) {
      setMemberError(errorMessage(e));
    } finally {
      setMemberBusy(false);
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
        {memberError && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() => setMemberError(null)}
          >
            {memberError}
          </Alert>
        )}
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
                      aria-label="Remove member"
                      disabled={memberBusy}
                      onClick={() => void removeMember(mid)}
                    >
                      <DeleteIcon />
                    </IconButton>
                  )
                }
              >
                <ListItemText
                  primary={actorDisplayName(m.display_name) || mid}
                  secondary={humanizeEnum(m.actor_type ?? m.type)}
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
            <Button disabled={!newActor.trim() || memberBusy} onClick={() => void addMember()}>
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

/** Render a single override leaf value as a readable string. */
function formatOverrideScalar(v: any): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) {
    if (v.length === 0) return "—";
    return v
      .map((x) => (x !== null && typeof x === "object" ? JSON.stringify(x) : String(x)))
      .join(", ");
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

/**
 * Flatten a profile-override object into readable "Parent · Child" label/value
 * rows (actors-orgs-12) so the panel shows a legible list instead of a raw
 * `JSON.stringify` dump. Nested objects are recursed; scalars/arrays become
 * one row each.
 */
function flattenOverrides(obj: any, prefix = ""): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  if (obj === null || obj === undefined) return out;
  if (typeof obj !== "object" || Array.isArray(obj)) {
    out.push([prefix || "Value", formatOverrideScalar(obj)]);
    return out;
  }
  for (const [k, v] of Object.entries(obj)) {
    const label = prefix ? `${prefix} · ${humanizeEnum(k)}` : humanizeEnum(k);
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flattenOverrides(v, label));
    } else {
      out.push([label, formatOverrideScalar(v)]);
    }
  }
  return out;
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
  const [teamError, setTeamError] = useState<string | null>(null);
  const [teamSaving, setTeamSaving] = useState(false);
  const [editOrgOpen, setEditOrgOpen] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [orgLabel, setOrgLabel] = useState("");
  const [orgEditError, setOrgEditError] = useState<string | null>(null);
  const [orgSaving, setOrgSaving] = useState(false);
  // Actor org-membership management (actors-orgs-10).
  const [addActorOpen, setAddActorOpen] = useState(false);
  const [newActorId, setNewActorId] = useState("");
  const [actorError, setActorError] = useState<string | null>(null);
  const [actorBusy, setActorBusy] = useState(false);

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
      setError(errorMessage(e));
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
      setOrgEditError(errorMessage(e));
    } finally {
      setOrgSaving(false);
    }
  };

  // Org membership is the actor's ``org_id`` property, set via the same admin
  // endpoint the actor-detail page uses. Adding here just stamps this org onto
  // the given actor; removing clears it. Both refetch to update the card + count.
  const addActorToOrg = async () => {
    if (!newActorId.trim()) return;
    setActorBusy(true);
    setActorError(null);
    try {
      await apiSend("PUT", `/admin/actors/${newActorId.trim()}/organization`, {
        org_id: orgId,
      });
      setAddActorOpen(false);
      setNewActorId("");
      void load();
    } catch (e) {
      setActorError(errorMessage(e));
    } finally {
      setActorBusy(false);
    }
  };

  const removeActorFromOrg = async (aid: string) => {
    if (!aid || !window.confirm("Remove this actor from the organization?")) return;
    setActorError(null);
    setActorBusy(true);
    try {
      await apiSend("PUT", `/admin/actors/${aid}/organization`, { org_id: null });
      void load();
    } catch (e) {
      setActorError(errorMessage(e));
    } finally {
      setActorBusy(false);
    }
  };

  const addTeam = async () => {
    if (!teamName.trim()) return;
    setTeamSaving(true);
    setTeamError(null);
    try {
      await apiSend("POST", "/admin/teams", {
        name: teamName.trim(),
        org_id: orgId,
      });
      setTeamName("");
      setAddTeamOpen(false);
      void load();
    } catch (e) {
      setTeamError(errorMessage(e));
    } finally {
      setTeamSaving(false);
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
                  <Button
                    size="small"
                    onClick={() => {
                      setTeamName("");
                      setTeamError(null);
                      setAddTeamOpen(true);
                    }}
                  >
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
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
              >
                <Typography variant="subtitle2">Actors</Typography>
                {authority >= 70 && (
                  <Button
                    size="small"
                    onClick={() => {
                      setNewActorId("");
                      setActorError(null);
                      setAddActorOpen(true);
                    }}
                  >
                    + Add actor
                  </Button>
                )}
              </Stack>
              {actorError && (
                <Alert
                  severity="error"
                  sx={{ my: 1 }}
                  onClose={() => setActorError(null)}
                >
                  {actorError}
                </Alert>
              )}
              <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", mt: 1 }}>
                {(data.actors ?? []).map((a, i) => {
                  const aid = String(a.actor_id ?? a.eb_id ?? a.id ?? "");
                  return (
                    <Chip
                      key={aid || i}
                      size="small"
                      title={a.display_name ?? a.actor_id}
                      label={
                        actorDisplayName(a.display_name) ||
                        actorDisplayName(a.actor_id) ||
                        "—"
                      }
                      onDelete={
                        authority >= 70 && aid && !actorBusy
                          ? () => void removeActorFromOrg(aid)
                          : undefined
                      }
                    />
                  );
                })}
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
            Object.entries(data.profile_overrides).map(([profileId, overrides]) => {
              const fields = flattenOverrides(overrides);
              return (
                <Box key={profileId} sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    {humanizeEnum(profileId)}
                  </Typography>
                  {fields.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No fields overridden.
                    </Typography>
                  ) : (
                    fields.map(([label, value], i) => (
                      <Stack
                        key={i}
                        direction="row"
                        spacing={2}
                        justifyContent="space-between"
                        sx={{ py: 0.25 }}
                      >
                        <Typography variant="body2" color="text.secondary">
                          {label}
                        </Typography>
                        <Typography
                          variant="body2"
                          sx={{ textAlign: "right", wordBreak: "break-word" }}
                        >
                          {value}
                        </Typography>
                      </Stack>
                    ))
                  )}
                </Box>
              );
            })
          ) : (
            <Typography variant="body2" color="text.secondary">
              No overrides — using default profile. Edit overrides requires
              authority &ge; 70 (form editor loads the resolved profile).
            </Typography>
          )}
        </AccordionDetails>
      </Accordion>

      <Dialog open={addActorOpen} onClose={() => setAddActorOpen(false)} fullWidth>
        <DialogTitle>Add actor to organization</DialogTitle>
        <DialogContent>
          {actorError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {actorError}
            </Alert>
          )}
          <TextField
            autoFocus
            fullWidth
            label="Actor ID"
            sx={{ mt: 1 }}
            value={newActorId}
            onChange={(e) => setNewActorId(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddActorOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!newActorId.trim() || actorBusy}
            onClick={() => void addActorToOrg()}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={addTeamOpen} onClose={() => setAddTeamOpen(false)} fullWidth>
        <DialogTitle>Add team</DialogTitle>
        <DialogContent>
          {teamError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {teamError}
            </Alert>
          )}
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
            disabled={!teamName.trim() || teamSaving}
            onClick={() => void addTeam()}
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
