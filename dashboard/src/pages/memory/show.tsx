// Fact Detail (`/memory/:id`) — everything about a single fact.
//
// Backend: `GET /dashboard/memory/{fact_id}/detail` -> FactDetailResponse
// (fact + resolved graph edges + linked claims + usage summary + session link).
// Mutations hit the runtime `/memory/{id}` endpoints directly.
//
// Implements plan Section 2 "Fact Detail" + SOW page 5.

import { useEffect, useMemo, useState, type FC } from "react";
import { useNavigate, useParams } from "react-router";
import {
  useApiUrl,
  useCustom,
  useCustomMutation,
  useGetIdentity,
  useNotification,
  usePermissions,
} from "@refinedev/core";
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
  DialogContentText,
  DialogTitle,
  Divider,
  Link as MuiLink,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import { formatDistanceToNow } from "date-fns";

import {
  AUTH_DELETE,
  AUTH_EDIT,
  CATEGORY_LABELS,
  MEMORY_CLASS_COLORS,
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  SCOPE_LABELS,
  type ChipColor,
  type FactDetailResponse,
  type FactEdge,
  type MemoryClass,
  type Scope,
} from "./types";

// ClaimStatus (elephantbroker/schemas/evidence.py) -> chip color.
const CLAIM_STATUS_COLORS: Record<string, ChipColor> = {
  unverified: "default",
  self_supported: "info",
  tool_supported: "success",
  supervisor_verified: "success",
  rejected: "error",
};

function relativeAge(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return String(iso);
  }
}

// Server should resolve human-readable labels; this is a defensive fallback so
// the page still reads well if only raw edges arrive.
function edgeSentence(edge: FactEdge): { text: string; nav?: string } {
  const label = edge.target_label || edge.target_id;
  const conf = (edge.target_properties?.confidence as number | undefined);
  const confSuffix = conf !== undefined ? ` (conf: ${Number(conf).toFixed(2)})` : "";
  const isActor = edge.target_type?.startsWith("Actor");
  const isGoal = edge.target_type?.startsWith("Goal");
  const isFact = edge.target_type?.startsWith("Fact");
  const nav = isActor
    ? `/actors/${edge.target_id}`
    : isGoal
      ? `/goals`
      : isFact
        ? `/memory/${edge.target_id}`
        : undefined;
  switch (edge.relation_type) {
    case "CREATED_BY":
      return { text: `Created by ${label}`, nav };
    case "ABOUT_ACTOR":
      return { text: `About ${label}`, nav };
    case "SERVES_GOAL":
      return { text: `Related to goal ${label}`, nav };
    case "SUPERSEDES":
      return {
        text:
          edge.direction === "outgoing"
            ? `Replaced ${label}${confSuffix}`
            : `Superseded by ${label}${confSuffix}`,
        nav,
      };
    case "CONTRADICTS":
      return { text: `Contradicts ${label}`, nav };
    case "SUPPORTS":
      return { text: `Evidence for claim ${label}`, nav };
    default:
      return { text: `${edge.relation_type}: ${label}`, nav };
  }
}

export const MemoryShow: FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const apiUrl = useApiUrl();
  const { open } = useNotification();
  const { data: permissions } = usePermissions<{ authorityLevel?: number }>();
  const { data: identity } = useGetIdentity<{ id?: string }>();
  const authorityLevel = permissions?.authorityLevel ?? 0;
  const canEdit = authorityLevel >= AUTH_EDIT;
  const canDelete = authorityLevel >= AUTH_DELETE;
  // Claims review (verify/reject) follows the edit threshold (>=50); the
  // archive toggle follows the delete threshold (>=70) per SOW 11.3.
  const canReviewClaims = canEdit;
  const canArchive = canDelete;

  const { data, isLoading, isError, refetch } = useCustom<FactDetailResponse>({
    url: `${apiUrl}/dashboard/memory/${id}/detail`,
    method: "get",
    queryOptions: { enabled: !!id },
  });

  const detail = data?.data;
  const fact = detail?.fact;

  // --- Inline edit -------------------------------------------------------
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [draftConfidence, setDraftConfidence] = useState<number>(1);
  const { mutate: customMutate } = useCustomMutation();
  const [deleteOpen, setDeleteOpen] = useState(false);

  // --- Claims review + archive toggle state ------------------------------
  // Local per-claim status overrides so verify/reject update inline without
  // waiting for (or racing) a full detail refetch.
  const [claimStatusOverrides, setClaimStatusOverrides] = useState<Record<string, string>>({});
  const [claimBusyId, setClaimBusyId] = useState<string | null>(null);
  const [rejectTargetId, setRejectTargetId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [archiveBusy, setArchiveBusy] = useState(false);

  useEffect(() => {
    if (fact) {
      setDraftText(fact.text);
      setDraftConfidence(fact.confidence);
    }
  }, [fact]);

  const notifyOk = (message: string) =>
    open?.({ type: "success", message, key: `fd-${Date.now()}` });
  const notifyErr = (message: string) =>
    open?.({ type: "error", message, key: `fd-err-${Date.now()}` });

  const saveEdit = () => {
    customMutate(
      {
        url: `${apiUrl}/memory/${id}`,
        method: "patch",
        values: { text: draftText, confidence: draftConfidence },
      },
      {
        onSuccess: () => {
          notifyOk("Fact updated");
          setEditing(false);
          refetch();
        },
        onError: () => notifyErr("Update failed"),
      },
    );
  };

  const doDelete = () => {
    customMutate(
      { url: `${apiUrl}/memory/${id}`, method: "delete", values: {} },
      {
        onSuccess: () => {
          notifyOk("Fact deleted");
          navigate("/memory");
        },
        onError: () => notifyErr("Delete failed"),
      },
    );
  };

  const copyId = () => {
    if (id) navigator.clipboard?.writeText(id);
    notifyOk("ID copied");
  };

  // --- Archive toggle (PATCH /memory/{id} { archived }) ------------------
  const toggleArchive = () => {
    const nextArchived = !fact?.archived;
    setArchiveBusy(true);
    customMutate(
      {
        url: `${apiUrl}/memory/${id}`,
        method: "patch",
        values: { archived: nextArchived },
      },
      {
        onSuccess: () => {
          notifyOk(nextArchived ? "Fact archived" : "Fact unarchived");
          refetch();
        },
        onError: () => notifyErr("Archive update failed"),
        onSettled: () => setArchiveBusy(false),
      },
    );
  };

  // --- Claims review (POST /claims/{id}/verify | /claims/{id}/reject) ----
  const verifyClaim = (claimId: string) => {
    setClaimBusyId(claimId);
    customMutate(
      { url: `${apiUrl}/claims/${claimId}/verify`, method: "post", values: {} },
      {
        onSuccess: (res) => {
          const status = (res?.data as { status?: string } | undefined)?.status;
          if (status) {
            setClaimStatusOverrides((prev) => ({ ...prev, [claimId]: status }));
          } else {
            refetch();
          }
          notifyOk("Claim verified");
        },
        onError: () => notifyErr("Verify failed"),
        onSettled: () => setClaimBusyId(null),
      },
    );
  };

  const submitReject = () => {
    // Backend requires a non-empty reason (EvidenceEngine.reject()).
    const claimId = rejectTargetId;
    const reason = rejectReason.trim();
    if (!claimId || !reason) return;
    setClaimBusyId(claimId);
    customMutate(
      {
        url: `${apiUrl}/claims/${claimId}/reject`,
        method: "post",
        values: {
          reason,
          ...(identity?.id ? { rejector_actor_id: identity.id } : {}),
        },
      },
      {
        onSuccess: (res) => {
          const status =
            (res?.data as { status?: string } | undefined)?.status ?? "rejected";
          setClaimStatusOverrides((prev) => ({ ...prev, [claimId]: status }));
          notifyOk("Claim rejected");
          setRejectTargetId(null);
          setRejectReason("");
        },
        onError: () => notifyErr("Reject failed"),
        onSettled: () => setClaimBusyId(null),
      },
    );
  };

  const edgeItems = useMemo(() => (detail?.edges ?? []).map(edgeSentence), [detail]);

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (isError || !fact) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load this fact.{" "}
        <MuiLink component="button" onClick={() => navigate("/memory")}>
          Back to Memory
        </MuiLink>
      </Alert>
    );
  }

  const cls = fact.memory_class as MemoryClass;
  const clsHex = MEMORY_CLASS_HEX[cls];
  const pct = Math.round((fact.confidence ?? 0) * 100);
  const usage = detail?.usage;
  const cogneeId = fact.cognee_data_id ?? fact.eb_id ?? null;

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5">Fact Detail</Typography>
        <Stack direction="row" spacing={1}>
          <Button onClick={() => navigate("/memory")}>Back</Button>
          {canEdit && !editing && (
            <Button variant="outlined" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {canDelete && (
            <Button variant="outlined" color="error" onClick={() => setDeleteOpen(true)}>
              Delete
            </Button>
          )}
        </Stack>
      </Stack>

      {/* Fact text */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          {editing ? (
            <Stack spacing={2}>
              <TextField
                label="Fact text"
                multiline
                minRows={3}
                fullWidth
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
              />
              <TextField
                label="Confidence (0–1)"
                type="number"
                inputProps={{ min: 0, max: 1, step: 0.05 }}
                value={draftConfidence}
                onChange={(e) => setDraftConfidence(Number(e.target.value))}
                sx={{ maxWidth: 200 }}
              />
              <Stack direction="row" spacing={1}>
                <Button variant="contained" onClick={saveEdit}>
                  Save
                </Button>
                <Button onClick={() => setEditing(false)}>Cancel</Button>
              </Stack>
            </Stack>
          ) : (
            <Typography variant="body1">{fact.text}</Typography>
          )}
        </CardContent>
      </Card>

      {/* Properties */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Properties
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Chip
              label={MEMORY_CLASS_LABELS[cls] ?? fact.memory_class}
              color={clsHex ? "default" : MEMORY_CLASS_COLORS[cls] ?? "default"}
              sx={clsHex ? { bgcolor: clsHex, color: "#fff" } : undefined}
            />
            <Chip variant="outlined" label={`Scope: ${SCOPE_LABELS[fact.scope as Scope] ?? fact.scope}`} />
            <Chip variant="outlined" label={`Category: ${CATEGORY_LABELS[fact.category] ?? fact.category}`} />
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 180 }}>
              <Typography variant="caption">Confidence</Typography>
              <LinearProgress
                variant="determinate"
                value={pct}
                sx={{ width: 100, height: 8, borderRadius: 1 }}
              />
              <Typography variant="caption">{pct}%</Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={3} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
            <Typography variant="body2" color="text.secondary">
              Created {relativeAge(fact.created_at)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Updated {relativeAge(fact.updated_at)}
            </Typography>
            {fact.decision_domain && (
              <Typography variant="body2" color="text.secondary">
                Domain: {fact.decision_domain}
              </Typography>
            )}
            {fact.archived && <Chip size="small" label="Archived" color="warning" />}
            {canArchive && (
              <Button
                size="small"
                variant="outlined"
                color={fact.archived ? "success" : "warning"}
                disabled={archiveBusy}
                onClick={toggleArchive}
              >
                {fact.archived ? "Unarchive" : "Archive"}
              </Button>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* Usage */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Usage
          </Typography>
          <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
            <Box>
              <Typography variant="h6">{usage?.use_count ?? fact.use_count}</Typography>
              <Typography variant="caption" color="text.secondary">
                times used
              </Typography>
            </Box>
            <Box>
              <Typography variant="h6">
                {usage?.successful_use_count ?? fact.successful_use_count}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                effective uses
              </Typography>
            </Box>
            <Box>
              <Typography variant="h6">
                {Math.round(
                  usage?.success_rate ??
                    (fact.use_count > 0
                      ? (fact.successful_use_count / fact.use_count) * 100
                      : 0),
                )}
                %
              </Typography>
              <Typography variant="caption" color="text.secondary">
                success rate
              </Typography>
            </Box>
            <Box>
              <Typography variant="h6">
                {relativeAge(usage?.last_used_at ?? fact.last_used_at)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                last used
              </Typography>
            </Box>
          </Stack>
          {usage?.superseded_by && (
            <Alert severity="info" sx={{ mt: 2 }}>
              Superseded by{" "}
              <MuiLink
                component="button"
                onClick={() => navigate(`/memory/${usage.superseded_by}`)}
              >
                {usage.superseded_by}
              </MuiLink>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Connections */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Connections
          </Typography>
          {edgeItems.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No connections.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {edgeItems.map((e, i) =>
                e.nav ? (
                  <MuiLink
                    key={i}
                    component="button"
                    sx={{ textAlign: "left" }}
                    onClick={() => e.nav && navigate(e.nav)}
                  >
                    {e.text}
                  </MuiLink>
                ) : (
                  <Typography key={i} variant="body2">
                    {e.text}
                  </Typography>
                ),
              )}
            </Stack>
          )}
        </CardContent>
      </Card>

      {/* Linked claims */}
      {(detail?.claims?.length ?? 0) > 0 && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" gutterBottom>
              Evidence for claims
            </Typography>
            <Stack spacing={1}>
              {detail!.claims.map((c) => {
                const status = claimStatusOverrides[c.claim_id] ?? c.status;
                const busy = claimBusyId === c.claim_id;
                return (
                  <Stack
                    key={c.claim_id}
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Typography variant="body2">{c.claim_text}</Typography>
                    <Chip
                      size="small"
                      label={status}
                      color={CLAIM_STATUS_COLORS[status] ?? "default"}
                    />
                    <Typography variant="caption" color="text.secondary">
                      {c.evidence_count} evidence
                    </Typography>
                    {/* REJECTED is terminal on the backend (EvidenceEngine.verify
                        refuses the transition), so hide review actions then. */}
                    {canReviewClaims && status !== "rejected" && (
                      <>
                        <Button
                          size="small"
                          variant="outlined"
                          color="success"
                          disabled={busy}
                          onClick={() => verifyClaim(c.claim_id)}
                        >
                          Verify
                        </Button>
                        <Button
                          size="small"
                          variant="outlined"
                          color="error"
                          disabled={busy}
                          onClick={() => {
                            setRejectReason("");
                            setRejectTargetId(c.claim_id);
                          }}
                        >
                          Reject
                        </Button>
                      </>
                    )}
                  </Stack>
                );
              })}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Session link */}
      {(detail?.session_key ?? fact.session_key) && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <MuiLink
              component="button"
              onClick={() =>
                navigate(
                  `/sessions/${detail?.session_key ?? fact.session_key}?highlight=${fact.id}&t=${fact.created_at}`,
                )
              }
            >
              View in session timeline ({detail?.session_key ?? fact.session_key})
            </MuiLink>
            {detail?.extraction_trace_event_id && (
              <Typography variant="caption" display="block" color="text.secondary">
                Extraction event: {detail.extraction_trace_event_id}
              </Typography>
            )}
          </CardContent>
        </Card>
      )}

      {/* Advanced accordions */}
      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Graph edges (raw)</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box component="pre" sx={{ overflow: "auto", fontSize: 12 }}>
            {JSON.stringify(detail?.edges ?? [], null, 2)}
          </Box>
        </AccordionDetails>
      </Accordion>
      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Identifiers</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={1}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                Fact ID: {fact.id}
              </Typography>
              <Tooltip title="Copy ID">
                <Button size="small" startIcon={<ContentCopyIcon />} onClick={copyId}>
                  Copy
                </Button>
              </Tooltip>
            </Stack>
            {cogneeId && (
              <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                cognee_data_id: {cogneeId}
              </Typography>
            )}
            {fact.session_id && (
              <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                session_id: {fact.session_id}
              </Typography>
            )}
          </Stack>
        </AccordionDetails>
      </Accordion>
      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Raw JSON</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box component="pre" sx={{ overflow: "auto", fontSize: 12 }}>
            {JSON.stringify(fact, null, 2)}
          </Box>
        </AccordionDetails>
      </Accordion>

      <Divider sx={{ my: 2 }} />

      {/* Delete confirmation */}
      <Dialog open={deleteOpen} onClose={() => setDeleteOpen(false)}>
        <DialogTitle>Delete this fact?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This permanently removes this fact from graph store, vector store, and all Cognee
            indexes. This cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteOpen(false)}>Cancel</Button>
          <Button color="error" onClick={doDelete}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Reject claim (reason required by the backend) */}
      <Dialog
        open={rejectTargetId !== null}
        onClose={() => setRejectTargetId(null)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Reject this claim?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Rejection is terminal — a rejected claim cannot be re-verified. A
            rejection reason is required and is recorded in the trace ledger.
          </DialogContentText>
          <TextField
            autoFocus
            required
            fullWidth
            multiline
            minRows={2}
            label="Rejection reason"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectTargetId(null)}>Cancel</Button>
          <Button
            color="error"
            disabled={!rejectReason.trim() || claimBusyId !== null}
            onClick={submitReject}
          >
            Reject
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default MemoryShow;
