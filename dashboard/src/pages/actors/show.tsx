// Actor detail page.
//
// Identity, org/teams, activity stats, handles, plus advanced accordions for
// relationships and authority chain. Edit / deactivate gated at authority >= 70.

import React, { useCallback, useEffect, useMemo, useState } from "react";
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
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
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
import { humanizeEnum } from "../../lib/format";
import { actorDisplayName, shortId } from "../../lib/labels";
import { errorMessage } from "../../lib/errors";

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

  // Org membership editing (actors-orgs-10). The org list is loaded once so the
  // card can resolve org_id -> a readable name AND feed the change dialog.
  const [orgOptions, setOrgOptions] = useState<
    Array<{ org_id?: string; name?: string; display_label?: string }>
  >([]);
  const [orgEditOpen, setOrgEditOpen] = useState(false);
  const [orgSelection, setOrgSelection] = useState("");
  const [orgSaving, setOrgSaving] = useState(false);
  const [orgError, setOrgError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(normalizeDetail(await apiGet(`/dashboard/actors/${actorId}/detail`)));
    } catch (e) {
      setError(errorMessage(e));
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
      setRelError(errorMessage(e));
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
      setChainError(errorMessage(e));
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
      setActionError(errorMessage(e));
    }
  };

  // Load the gateway's org list once so the card shows a name (not a raw UUID)
  // and the change dialog has options. READ-gated, same as the actor detail.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiGet<{ organizations?: any[] }>(
          "/dashboard/organizations",
        );
        if (!cancelled) setOrgOptions(res.organizations ?? []);
      } catch {
        /* org selector degrades to raw ids; non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const orgDisplay = useMemo(() => {
    if (data?.organization_name) return data.organization_name;
    const oid = data?.org_id ? String(data.org_id) : "";
    if (!oid) return "—";
    const match = orgOptions.find((o) => String(o.org_id) === oid);
    return match ? match.name || oid : oid;
  }, [data, orgOptions]);

  const openOrgEdit = () => {
    setOrgError(null);
    setOrgSelection(data?.org_id ? String(data.org_id) : "");
    setOrgEditOpen(true);
  };

  const saveOrg = async () => {
    setOrgSaving(true);
    setOrgError(null);
    try {
      // PUT /admin/actors/{id}/organization — org_id: null clears membership.
      await apiSend("PUT", `/admin/actors/${actorId}/organization`, {
        org_id: orgSelection || null,
      });
      setOrgEditOpen(false);
      void load();
    } catch (e) {
      setOrgError(errorMessage(e));
    } finally {
      setOrgSaving(false);
    }
  };

  if (loading) return <CircularProgress sx={{ m: 4 }} />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;

  const type = data.actor_type ?? data.type;
  const nameOf = (aid: string) => {
    const raw = actorNames[aid];
    if (raw) return actorDisplayName(raw);
    return aid ? `${shortId(aid)}…` : "unknown";
  };

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
    // Not every relationship's other end is an actor: OWNS_GOAL points at a
    // goal node and OWNS_ARTIFACT at an artifact node. Linking those to
    // /actors/{id} dead-ends on a 404 (actors-orgs-6), so route goals to the
    // goal's facts and render artifacts as a non-navigable chip.
    const rt = (r.relationship_type || "").toLowerCase();
    const isGoal = rt === "owns_goal";
    const isArtifact = rt === "owns_artifact";
    const target = isGoal
      ? {
          label: `goal ${shortId(otherId)}`,
          onClick: () =>
            push(`/memory?goal_id=${encodeURIComponent(otherId)}`),
        }
      : isArtifact
        ? { label: `artifact ${shortId(otherId)}`, onClick: undefined }
        : {
            label: nameOf(otherId),
            onClick: () => push(`/actors/${otherId}`),
          };
    return (
      <Stack key={key} direction="row" spacing={1} alignItems="center">
        <Chip
          size="small"
          variant="outlined"
          color={relationshipColor(r.relationship_type)}
          label={humanizeEnum(r.relationship_type)}
        />
        <Typography variant="body2" color="text.secondary">
          {direction === "outgoing" ? "→" : "←"}
        </Typography>
        {target.onClick ? (
          <Button size="small" onClick={target.onClick}>
            {target.label}
          </Button>
        ) : (
          <Chip size="small" label={target.label} />
        )}
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
          <Typography variant="h5" title={data.display_name}>
            {actorDisplayName(data.display_name)}
          </Typography>
          <Chip
            label={humanizeEnum(type) || "—"}
            color={actorTypeColor(type)}
            size="small"
          />
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
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="flex-start"
            >
              <Typography variant="subtitle2" gutterBottom>
                Organization & Teams
              </Typography>
              {authority >= 70 && (
                <IconButton
                  size="small"
                  aria-label="Change organization"
                  sx={{ my: -0.5 }}
                  onClick={openOrgEdit}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              )}
            </Stack>
            <Typography variant="body2">{orgDisplay}</Typography>
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
                      label={actorDisplayName(data.display_name)}
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
                        label={
                          actorDisplayName(a.display_name) ||
                          nameOf(String(a.id ?? ""))
                        }
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

      <Dialog
        open={orgEditOpen}
        onClose={() => setOrgEditOpen(false)}
        fullWidth
      >
        <DialogTitle>Set organization</DialogTitle>
        <DialogContent>
          {orgError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {orgError}
            </Alert>
          )}
          <FormControl fullWidth size="small" sx={{ mt: 1 }}>
            <InputLabel id="actor-org-label">Organization</InputLabel>
            <Select
              labelId="actor-org-label"
              label="Organization"
              value={orgSelection}
              onChange={(e) => setOrgSelection(String(e.target.value))}
            >
              <MenuItem value="">None (no organization)</MenuItem>
              {orgOptions.map((o) => (
                <MenuItem key={String(o.org_id)} value={String(o.org_id)}>
                  {o.name}
                  {o.display_label ? ` — ${o.display_label}` : ""}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOrgEditOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={orgSaving} onClick={saveOrg}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ActorShowPage;
