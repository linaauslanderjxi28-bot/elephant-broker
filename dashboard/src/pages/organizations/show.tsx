// Organization detail page.
//
// Org info, teams with expandable member lists (add / remove members), and a
// form-based profile-override editor. Team management gated at authority >= 50;
// override editing and org edit at >= 70.

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
  onChanged: () => void;
  canManage: boolean;
}) {
  const { team } = props;
  const teamId = String(team.team_id ?? team.eb_id ?? team.id ?? "");
  const [members, setMembers] = useState<any[] | null>(null);
  const [newActor, setNewActor] = useState("");

  const loadMembers = useCallback(async () => {
    try {
      const res = await apiGet<any>(`/admin/teams/${teamId}/members`);
      setMembers(Array.isArray(res) ? res : (res.items ?? res.members ?? []));
    } catch {
      setMembers([]);
    }
  }, [teamId]);

  return (
    <Accordion
      onChange={(_, exp) => {
        if (exp && members === null) void loadMembers();
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography sx={{ flex: 1 }}>
          {team.name ?? team.display_label}
        </Typography>
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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet(`/dashboard/organizations/${orgId}`));
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

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        {data.name}
        {data.display_label ? ` — ${data.display_label}` : ""}
      </Typography>

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
                  onChanged={load}
                  canManage={authority >= 50}
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
    </Box>
  );
};

export default OrganizationShowPage;
