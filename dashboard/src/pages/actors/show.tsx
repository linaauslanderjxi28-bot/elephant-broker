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
import EditIcon from "@mui/icons-material/Edit";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  actorTypeColor,
  apiGet,
  apiSend,
  authorityLabel,
  relativeTime,
  useAuthority,
} from "../home/dashboardApi";
import { EditActorDialog } from "./list";

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
  team_ids?: string[];
  team_names?: string[];
  handles?: string[];
  fact_count?: number;
  goals_owned?: number;
  last_active?: string;
}

/** Row shape of GET /actors/{id}/relationships (ActorRelationship). */
interface RelationshipRow {
  source_actor_id: string;
  target_actor_id: string;
  relationship_type: string;
}

/** Row shape of GET /actors/{id}/authority-chain (ActorRef). */
interface ChainActor {
  id: string;
  type?: string;
  display_name?: string;
  authority_level?: number;
}

type ChipColor =
  | "default"
  | "primary"
  | "secondary"
  | "error"
  | "info"
  | "success"
  | "warning";

function relationshipColor(relType: string): ChipColor {
  switch ((relType || "").toLowerCase()) {
    case "supervises":
    case "reports_to":
      return "primary";
    case "delegates_to":
    case "requested_by":
      return "info";
    case "trusts":
    case "collaborates_with":
      return "success";
    case "blocks":
      return "error";
    default:
      return "default";
  }
}

/**
 * GET /dashboard/actors/{id}/detail returns a nested ActorDetailResponse
 * ({ actor: {...}, team_ids, org_id, fact_count, last_active }); older builds
 * returned a flat object. Normalize both into one flat ActorDetail.
 */
function normalizeDetail(res: any): ActorDetail {
  const a =
    res && typeof res.actor === "object" && res.actor !== null
      ? res.actor
      : (res ?? {});
  return {
    ...a,
    fact_count: res?.fact_count ?? a.fact_count,
    last_active: res?.last_active ?? a.last_active,
    org_id: a.org_id ?? res?.org_id,
    team_ids: res?.team_ids ?? a.team_ids ?? [],
  };
}

export const ActorShowPage: React.FC = () => {
  const { id } = useParsed();
  const actorId = String(id ?? "");
  const authority = useAuthority();
  const { push } = useNavigation();
  const [data, setData] = useState<ActorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [relationships, setRelationships] = useState<RelationshipRow[] | null>(
    null,
  );
  const [relError, setRelError] = useState<string | null>(null);
  const [actorNames, setActorNames] = useState<Record<string, string>>({});
  const [authChain, setAuthChain] = useState<ChainActor[] | null>(null);
  const [chainError, setChainError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(normalizeDetail(await apiGet(`/dashboard/actors/${actorId}/detail`)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [actorId]);

  useEffect(() => {
    if (actorId) void load();
    // Reset lazily-loaded accordion data when navigating between actors.
    setRelationships(null);
    setRelError(null);
    setAuthChain(null);
    setChainError(null);
    setActionError(null);
  }, [actorId, load]);

  const loadRelationships = useCallback(async () => {
    try {
      const res = await apiGet<RelationshipRow[]>(
        `/actors/${actorId}/relationships`,
      );
      setRelationships(Array.isArray(res) ? res : []);
    } catch (e) {
      setRelError((e as Error).message);
      setRelationships([]);
    }
    // Best-effort id -> display name map for readable relationship rows.
    try {
      const list = await apiGet<any>("/dashboard/actors");
      const arr = Array.isArray(list) ? list : (list.actors ?? []);
      const map: Record<string, string> = {};
      for (const a of arr) {
        const aid = String(a.actor_id ?? a.eb_id ?? a.id ?? "");
        if (aid) map[aid] = a.display_name || aid;
      }
      setActorNames(map);
    } catch {
      /* names stay as shortened ids */
    }
  }, [actorId]);

  const loadAuthChain = useCallback(async () => {
    try {
      const res = await apiGet<ChainActor[]>(
        `/actors/${actorId}/authority-chain`,
      );
      setAuthChain(Array.isArray(res) ? res : []);
    } catch (e) {
      setChainError((e as Error).message);
      setAuthChain([]);
    }
  }, [actorId]);

  const setActive = async (active: boolean) => {
    if (
      !active &&
      !window.confirm("Deactivate this actor and revoke its sessions?")
    )
      return;
    setActionError(null);
    try {
      await apiSend("PUT", `/admin/actors/${actorId}/status`, { active });
      void load();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  if (loading) return <CircularProgress sx={{ m: 4 }} />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;

  const type = data.actor_type ?? data.type;
  const nameOf = (aid: string) =>
    actorNames[aid] ?? (aid ? `${aid.slice(0, 8)}…` : "unknown");

  const outgoing = (relationships ?? []).filter(
    (r) => String(r.source_actor_id) === actorId,
  );
  const incoming = (relationships ?? []).filter(
    (r) =>
      String(r.target_actor_id) === actorId &&
      String(r.source_actor_id) !== actorId,
  );

  const relationshipRow = (
    r: RelationshipRow,
    direction: "outgoing" | "incoming",
    key: string,
  ) => {
    const otherId =
      direction === "outgoing"
        ? String(r.target_actor_id)
        : String(r.source_actor_id);
    return (
      <Stack key={key} direction="row" spacing={1} alignItems="center">
        <Chip
          size="small"
          variant="outlined"
          color={relationshipColor(r.relationship_type)}
          label={(r.relationship_type || "").replace(/_/g, " ")}
        />
        <Typography variant="body2" color="text.secondary">
          {direction === "outgoing" ? "→" : "←"}
        </Typography>
        <Button size="small" onClick={() => push(`/actors/${otherId}`)}>
          {nameOf(otherId)}
        </Button>
      </Stack>
    );
  };

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
        {authority >= 70 && (
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              startIcon={<EditIcon />}
              onClick={() => setEditOpen(true)}
            >
              Edit
            </Button>
            {data.active === false ? (
              <Button variant="outlined" onClick={() => setActive(true)}>
                Reactivate
              </Button>
            ) : (
              <Button
                color="error"
                variant="outlined"
                onClick={() => setActive(false)}
              >
                Deactivate
              </Button>
            )}
          </Stack>
        )}
      </Stack>

      {actionError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

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
              {(data.team_names ?? data.team_ids ?? []).map((t) => (
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
        onChange={(_, exp) => {
          if (exp && relationships === null) void loadRelationships();
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Relationships</Typography>
        </AccordionSummary>
        <AccordionDetails>
          {relationships === null ? (
            <CircularProgress size={20} />
          ) : (
            <Stack spacing={2}>
              {relError && <Alert severity="error">{relError}</Alert>}
              {!relError &&
                outgoing.length === 0 &&
                incoming.length === 0 && (
                  <Typography variant="body2" color="text.secondary">
                    No relationships recorded for this actor.
                  </Typography>
                )}
              {outgoing.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" gutterBottom>
                    Outgoing
                  </Typography>
                  <Stack spacing={1}>
                    {outgoing.map((r, i) =>
                      relationshipRow(r, "outgoing", `out-${i}`),
                    )}
                  </Stack>
                </Box>
              )}
              {incoming.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" gutterBottom>
                    Incoming
                  </Typography>
                  <Stack spacing={1}>
                    {incoming.map((r, i) =>
                      relationshipRow(r, "incoming", `in-${i}`),
                    )}
                  </Stack>
                </Box>
              )}
            </Stack>
          )}
        </AccordionDetails>
      </Accordion>

      <Accordion
        onChange={(_, exp) => {
          if (exp && authChain === null) void loadAuthChain();
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Authority chain</Typography>
        </AccordionSummary>
        <AccordionDetails>
          {authChain === null ? (
            <CircularProgress size={20} />
          ) : (
            <Stack spacing={1}>
              {chainError && <Alert severity="error">{chainError}</Alert>}
              {!chainError && authChain.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No supervisors above this actor.
                </Typography>
              )}
              {authChain.length > 0 && (
                <>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip
                      size="small"
                      color="primary"
                      label={data.display_name}
                    />
                    <Typography variant="caption" color="text.secondary">
                      this actor
                    </Typography>
                  </Stack>
                  {authChain.map((a, i) => (
                    <Stack
                      key={a.id ?? i}
                      direction="row"
                      spacing={1}
                      alignItems="center"
                      sx={{ pl: (i + 1) * 2 }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        ↑ reports to
                      </Typography>
                      <Chip
                        size="small"
                        label={a.display_name || nameOf(String(a.id ?? ""))}
                        onClick={() => a.id && push(`/actors/${a.id}`)}
                      />
                      <Chip
                        size="small"
                        variant="outlined"
                        label={authorityLabel(Number(a.authority_level ?? 0))}
                      />
                    </Stack>
                  ))}
                </>
              )}
            </Stack>
          )}
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

      <EditActorDialog
        open={editOpen}
        actor={
          data
            ? {
                actor_id: actorId,
                display_name: data.display_name,
                actor_type: type,
                authority_level: data.authority_level,
                handles: data.handles,
              }
            : null
        }
        onClose={() => setEditOpen(false)}
        onSaved={load}
      />
    </Box>
  );
};

export default ActorShowPage;
