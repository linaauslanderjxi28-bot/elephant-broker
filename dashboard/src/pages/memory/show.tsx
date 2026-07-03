// Fact Detail (`/memory/:id`) — everything about a single fact.
//
// Backend: `GET /dashboard/memory/{fact_id}/detail` -> FactDetailResponse
// (fact + resolved graph edges + linked claims + usage summary + session link).
// Mutations hit the runtime `/memory/{id}` endpoints directly.
//
// Implements plan Section 2 "Fact Detail" + SOW page 5.

import { useEffect, useMemo, useState, type FC, type SyntheticEvent } from "react";
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

import { errorMessage } from "../../lib/errors";
import { formatRelativeTime, humanizeEnum } from "../../lib/format";
import { actorDisplayName } from "../../lib/labels";
import {
  AUTH_DELETE,
  AUTH_EDIT,
  CATEGORY_LABELS,
  MEMORY_CLASS_COLORS,
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  SCOPE_LABELS,
  type ChipColor,
  type ClaimDetailResponse,
  type FactDetailResponse,
  type FactEdge,
  type LinkedClaim,
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

// EvidenceType (elephantbroker/schemas/evidence.py) -> chip color.
const EVIDENCE_TYPE_COLORS: Record<string, ChipColor> = {
  tool_output: "success",
  chunk_ref: "info",
  supervisor_sign_off: "secondary",
  external_link: "default",
};

function relativeAge(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return String(iso);
  }
}

// Turn a raw graph edge into a human sentence + (only when resolvable) a nav
// target. The backend frequently returns a generic `target_type` of `__Node__`
// (Cognee tags every node with that label, and `labels(t)[0]` can pick it),
// which used to leave every connection as un-clickable dead text
// (memory-browse-4). We therefore resolve the destination from the *relation
// type* — which reliably implies the target kind — and only fall back to
// `target_type` for unknown relations; a row is a link ONLY when it resolves to
// a real page, never a dead link.
function edgeSentence(edge: FactEdge): { text: string; nav?: string } {
  const raw = edge.target_label || edge.target_id || "";
  const label = actorDisplayName(raw) || raw;
  const id = edge.target_id || "";
  const conf = edge.target_properties?.confidence as number | undefined;
  const confSuffix = conf !== undefined ? ` (conf: ${Number(conf).toFixed(2)})` : "";

  const actorNav = id ? `/actors/${id}` : undefined;
  const factNav = id ? `/memory/${id}` : undefined;
  const goalNav = id ? `/goals?highlight=${encodeURIComponent(id)}` : undefined;

  switch (edge.relation_type) {
    case "CREATED_BY":
      return { text: `Created by ${label}`, nav: actorNav };
    case "ABOUT_ACTOR":
      return { text: `About ${label}`, nav: actorNav };
    case "SERVES_GOAL":
      return { text: `Related to goal ${label}`, nav: goalNav };
    case "SUPERSEDES":
      return {
        text:
          edge.direction === "outgoing"
            ? `Replaced ${label}${confSuffix}`
            : `Superseded by ${label}${confSuffix}`,
        nav: factNav,
      };
    case "CONTRADICTS":
      return { text: `Contradicts ${label}`, nav: factNav };
    case "SUPPORTS":
      // Claims have no standalone page — render as text, never a dead link.
      return { text: `Evidence for claim ${label}` };
    default: {
      // Unknown relation: only offer a link when `target_type` names a concrete,
      // routable kind; otherwise show plain humanized text.
      const tt = edge.target_type ?? "";
      const nav = tt.startsWith("Actor")
        ? actorNav
        : tt.startsWith("Goal")
          ? goalNav
          : tt.startsWith("Fact")
            ? factNav
            : undefined;
      return { text: `${humanizeEnum(edge.relation_type)}: ${label}`, nav };
    }
  }
}

// One linked-claim row in the Fact Detail claims panel. The collapsed summary
// keeps the review affordance (claim text + status chip + evidence count +
// Verify/Reject) exactly as before; expanding lazily pulls the full claim record
// (`GET /claims/{id}`) so a reviewer can read the actual evidence receipts and,
// for a rejected claim, WHY it was rejected — BEFORE acting. The GET is cached
// per claim (react-query, staleTime Infinity) so re-expanding never refetches.
const ClaimRow: FC<{
  claim: LinkedClaim;
  status: string;
  busy: boolean;
  canReview: boolean;
  onVerify: () => void;
  onReject: () => void;
}> = ({ claim, status, busy, canReview, onVerify, onReject }) => {
  const apiUrl = useApiUrl();
  const [expanded, setExpanded] = useState(false);

  // Lazy fetch: `enabled` only flips true once the row is first expanded.
  const { data, isLoading, isError } = useCustom<ClaimDetailResponse>({
    url: `${apiUrl}/claims/${claim.claim_id}`,
    method: "get",
    queryOptions: {
      enabled: expanded,
      // Evidence for a claim is effectively immutable within a review session;
      // hold it forever so collapsing/re-expanding is instant and never refetches.
      staleTime: Infinity,
    },
  });
  const detail = data?.data;

  // Verify/Reject live inside the AccordionSummary (a button region), so their
  // clicks must NOT bubble up and toggle the accordion.
  const stopToggle = (e: SyntheticEvent) => e.stopPropagation();

  return (
    <Accordion
      expanded={expanded}
      onChange={(_e, isExpanded) => setExpanded(isExpanded)}
      disableGutters
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          sx={{ width: "100%", pr: 1 }}
        >
          <Typography variant="body2">{claim.claim_text}</Typography>
          <Chip
            size="small"
            label={humanizeEnum(status)}
            color={CLAIM_STATUS_COLORS[status] ?? "default"}
          />
          <Typography variant="caption" color="text.secondary">
            {claim.evidence_count} evidence
          </Typography>
          {/* REJECTED is terminal on the backend (EvidenceEngine.verify refuses
              the transition), so hide review actions then. */}
          {canReview && status !== "rejected" && (
            <>
              <Button
                size="small"
                variant="outlined"
                color="success"
                disabled={busy}
                onMouseDown={stopToggle}
                onClick={(e) => {
                  e.stopPropagation();
                  onVerify();
                }}
              >
                Verify
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="error"
                disabled={busy}
                onMouseDown={stopToggle}
                onClick={(e) => {
                  e.stopPropagation();
                  onReject();
                }}
              >
                Reject
              </Button>
            </>
          )}
        </Stack>
      </AccordionSummary>
      <AccordionDetails>
        {isLoading ? (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 1 }}>
            <CircularProgress size={16} />
            <Typography variant="caption" color="text.secondary">
              Loading evidence…
            </Typography>
          </Stack>
        ) : isError ? (
          <Alert severity="warning" variant="outlined">
            Could not load evidence for this claim.
          </Alert>
        ) : (
          <Stack spacing={2}>
            {/* Why it was rejected — the durable rejection_reason. */}
            {detail?.rejection_reason && (
              <Alert severity="error">
                <Typography variant="caption" sx={{ fontWeight: 600, display: "block" }}>
                  Rejection reason
                </Typography>
                {detail.rejection_reason}
              </Alert>
            )}
            <Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600 }}
              >
                Evidence
              </Typography>
              {(detail?.evidence_refs?.length ?? 0) === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  No evidence attached to this claim.
                </Typography>
              ) : (
                <Stack spacing={1} sx={{ mt: 0.5 }}>
                  {detail!.evidence_refs.map((ev) => {
                    const when = formatRelativeTime(ev.created_at);
                    return (
                      <Stack
                        key={ev.id}
                        direction="row"
                        spacing={1}
                        alignItems="flex-start"
                        flexWrap="wrap"
                        useFlexGap
                      >
                        <Chip
                          size="small"
                          label={humanizeEnum(ev.type)}
                          color={EVIDENCE_TYPE_COLORS[ev.type] ?? "default"}
                        />
                        <Typography
                          variant="body2"
                          sx={{
                            fontFamily: "monospace",
                            wordBreak: "break-all",
                            flex: 1,
                            minWidth: 0,
                          }}
                        >
                          {ev.ref_value}
                        </Typography>
                        {ev.created_at && (
                          <Tooltip title={when.title}>
                            <Typography variant="caption" color="text.secondary">
                              {when.text}
                            </Typography>
                          </Tooltip>
                        )}
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Box>
          </Stack>
        )}
      </AccordionDetails>
    </Accordion>
  );
};

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
    queryOptions: {
      enabled: !!id,
      // memory-browse-9: an invalid/unknown fact id used to spin for ~15s while
      // react-query retried a 404 three times. Fail fast on any 4xx; still allow
      // a couple of retries for transient network / 5xx errors.
      retry: (failureCount, err) => {
        const status = (err as { statusCode?: number })?.statusCode;
        if (status !== undefined && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
    },
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
  // A no-op verify (no new evidence, nothing to promote) is neither a success
  // nor an error. Refine-MUI forwards `type` verbatim to notistack's `variant`,
  // which renders "info" as a neutral blue toast; Refine-core's param union is
  // narrower, so widen through a typed local before the (safe) cast (gap-4-8).
  const notifyInfo = (message: string) => {
    const type: "success" | "error" | "progress" | "info" = "info";
    open?.({ type: type as "success", message, key: `fd-info-${Date.now()}` });
  };

  // Every mutation below surfaces exactly ONE toast via notifyOk/notifyErr.
  // Refine's useCustomMutation ALSO auto-fires its own success/error toast
  // (raw `Error (status code: N)` + backend detail) unless told not to — that
  // duplicate was the second overlapping error surface in gap-4-5. Suppress it.
  const SILENT = { successNotification: false, errorNotification: false } as const;

  const saveEdit = () => {
    customMutate(
      {
        url: `${apiUrl}/memory/${id}`,
        method: "patch",
        values: { text: draftText, confidence: draftConfidence },
        ...SILENT,
      },
      {
        onSuccess: () => {
          notifyOk("Fact updated");
          setEditing(false);
          refetch();
        },
        onError: (err) => notifyErr(errorMessage(err)),
      },
    );
  };

  const doDelete = () => {
    customMutate(
      { url: `${apiUrl}/memory/${id}`, method: "delete", values: {}, ...SILENT },
      {
        onSuccess: () => {
          notifyOk("Fact deleted");
          navigate("/memory");
        },
        onError: (err) => notifyErr(errorMessage(err)),
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
        ...SILENT,
      },
      {
        onSuccess: () => {
          notifyOk(nextArchived ? "Fact archived" : "Fact unarchived");
          refetch();
        },
        onError: (err) => notifyErr(errorMessage(err)),
        onSettled: () => setArchiveBusy(false),
      },
    );
  };

  // --- Claims review (POST /claims/{id}/verify | /claims/{id}/reject) ----
  const verifyClaim = (claimId: string, priorStatus: string) => {
    setClaimBusyId(claimId);
    customMutate(
      { url: `${apiUrl}/claims/${claimId}/verify`, method: "post", values: {}, ...SILENT },
      {
        onSuccess: (res) => {
          const status = (res?.data as { status?: string } | undefined)?.status;
          if (status) {
            setClaimStatusOverrides((prev) => ({ ...prev, [claimId]: status }));
          } else {
            refetch();
          }
          // The backend only promotes a claim when it has (new) supporting
          // evidence; re-verifying an evidence-less or already-settled claim is
          // a no-op (EvidenceEngine.verify leaves the status untouched). Only
          // announce success on a real transition; otherwise say nothing
          // happened instead of a misleading "verified" (gap-4-8).
          if (status && status !== priorStatus) {
            notifyOk(`Claim ${humanizeEnum(status).toLowerCase()}`);
          } else {
            notifyInfo("No change — this claim has no new evidence to verify.");
          }
        },
        onError: (err) => notifyErr(errorMessage(err)),
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
        ...SILENT,
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
        onError: (err) => notifyErr(errorMessage(err)),
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
                  <ClaimRow
                    key={c.claim_id}
                    claim={c}
                    status={status}
                    busy={busy}
                    canReview={canReviewClaims}
                    onVerify={() => verifyClaim(c.claim_id, status)}
                    onReject={() => {
                      setRejectReason("");
                      setRejectTargetId(c.claim_id);
                    }}
                  />
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
