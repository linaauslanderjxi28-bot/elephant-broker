// Actor detail page.
//
// Identity, org/teams, activity stats, handles, plus advanced accordions for
// relationships and authority chain. Edit / deactivate gated at authority >= 70.

import React, { useCallback, useEffect, useState } from "react";
import { useNavigation, useParsed } from "@refinedev/core";
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
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  actorTypeColor,
  apiGet,
  apiSend,
  authorityLabel,
  relativeTime,
  useAuthority,
} from "../home/dashboardApi";

interface ActorDetail {
  actor_id?: string;
  eb_id?: string;
  display_name?: string;
  actor_type?: string;
  type?: string;
  authority_level?: number;
  active?: boolean;
  org_id?: string;
  organization_name?: string;
  team_names?: string[];
  handles?: string[];
  fact_count?: number;
  goals_owned?: number;
  last_active?: string;
}

export const ActorShowPage: React.FC = () => {
  const { id } = useParsed();
  const actorId = String(id ?? "");
  const authority = useAuthority();
  const { push } = useNavigation();
  const [data, setData] = useState<ActorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [relationships, setRelationships] = useState<any>(null);
  const [authChain, setAuthChain] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet(`/dashboard/actors/${actorId}/detail`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [actorId]);

  useEffect(() => {
    if (actorId) void load();
  }, [actorId, load]);

  const setActive = async (active: boolean) => {
    if (
      !active &&
      !window.confirm("Deactivate this actor and revoke its sessions?")
    )
      return;
    await apiSend("PUT", `/admin/actors/${actorId}`, { active });
    void load();
  };

  if (loading) return <CircularProgress sx={{ m: 4 }} />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;

  const type = data.actor_type ?? data.type;

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Stack direction="row" spacing={2} alignItems="center">
          <Typography variant="h5">{data.display_name}</Typography>
          <Chip label={type} color={actorTypeColor(type)} size="small" />
          <Chip
            label={authorityLabel(data.authority_level ?? 0)}
            variant="outlined"
            size="small"
          />
          {data.active === false ? (
            <Chip label="inactive" size="small" />
          ) : (
            <Chip label="active" color="success" size="small" />
          )}
        </Stack>
        {authority >= 70 &&
          (data.active === false ? (
            <Button variant="outlined" onClick={() => setActive(true)}>
              Reactivate
            </Button>
          ) : (
            <Button color="error" variant="outlined" onClick={() => setActive(false)}>
              Deactivate
            </Button>
          ))}
      </Stack>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" },
        }}
      >
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              Organization & Teams
            </Typography>
            <Typography variant="body2">
              {data.organization_name ?? data.org_id ?? "—"}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: "wrap" }}>
              {(data.team_names ?? []).map((t) => (
                <Chip key={t} size="small" label={t} />
              ))}
            </Stack>
          </CardContent>
        </Card>
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              Activity
            </Typography>
            <Typography variant="body2">
              Created {data.fact_count ?? 0} facts
            </Typography>
            <Typography variant="body2">
              Owns {data.goals_owned ?? 0} goals
            </Typography>
            <Typography variant="body2">
              Last active {relativeTime(data.last_active)}
            </Typography>
            <Button
              size="small"
              sx={{ mt: 1 }}
              onClick={() => push(`/memory?source_actor_id=${actorId}`)}
            >
              View all facts
            </Button>
          </CardContent>
        </Card>
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              Handles
            </Typography>
            <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
              {(data.handles ?? []).map((h) => (
                <Chip key={h} size="small" label={h} />
              ))}
              {(data.handles ?? []).length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  None
                </Typography>
              )}
            </Stack>
          </CardContent>
        </Card>
      </Box>

      <Accordion
        sx={{ mt: 2 }}
        onChange={async (_, exp) => {
          if (exp && !relationships) {
            try {
              setRelationships(
                await apiGet(`/actors/${actorId}/relationships`),
              );
            } catch {
              setRelationships({});
            }
          }
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Relationships</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {relationships
              ? JSON.stringify(relationships, null, 2)
              : "Loading..."}
          </pre>
        </AccordionDetails>
      </Accordion>

      <Accordion
        onChange={async (_, exp) => {
          if (exp && !authChain) {
            try {
              setAuthChain(
                await apiGet(`/actors/${actorId}/authority-chain`),
              );
            } catch {
              setAuthChain({});
            }
          }
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Authority chain</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {authChain ? JSON.stringify(authChain, null, 2) : "Loading..."}
          </pre>
        </AccordionDetails>
      </Accordion>

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Raw JSON</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Divider sx={{ mb: 1 }} />
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {JSON.stringify(data, null, 2)}
          </pre>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default ActorShowPage;
