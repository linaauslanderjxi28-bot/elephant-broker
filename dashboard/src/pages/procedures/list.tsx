// Procedures page.
//
// Lists procedure definitions with an expandable detail panel (steps, active
// executions, recent completions) and a creation form with full step editor.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
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
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import {
  apiGet,
  apiSend,
  DECISION_DOMAINS,
  scopesForAuthority,
  useAuthority,
} from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";
import { humanizeEnum } from "../../lib/format";

interface StepDraft {
  instruction: string;
  proof_required: boolean;
  proof_type: string;
  optional: boolean;
}
interface Procedure {
  id?: string;
  eb_id?: string;
  procedure_id?: string;
  name: string;
  description?: string;
  scope?: string;
  // /dashboard/procedures (ProcedureSummary) returns only the fields above plus
  // execution_count (currently hardcoded 0 server-side). It does NOT return
  // enabled / steps / active executions / completion counts — see the
  // cross-file needs for goals-procedures-5 and -8.
  execution_count?: number;
}

// ProofType enum values are lowercase (schemas/procedure.py ProofType); send
// those, render humanized labels.
const PROOF_TYPES = [
  "diff_hash",
  "chunk_ref",
  "receipt",
  "version_record",
  "supervisor_sign_off",
];

function procId(p: Procedure): string {
  return String(p.procedure_id ?? p.eb_id ?? p.id ?? "");
}

function DetailPanel({ id }: { id: string }) {
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void (async () => {
      try {
        setDetail(await apiGet(`/dashboard/procedures/${id}/detail`));
      } catch {
        setDetail({});
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) return <CircularProgress size={20} sx={{ m: 2 }} />;
  // GET /dashboard/procedures/{id}/detail returns a ProcedureDetailResponse:
  // { procedure, steps: ProcedureStep[], active_execution_ids: string[],
  //   audit_trail: dict[], note }. Steps are serialized ProcedureStep records
  // (step_id / order / instruction / required_evidence / is_optional) —
  // goals-procedures-9 previously read proof_required / proof_type / optional
  // which never exist on the wire.
  const steps: any[] = Array.isArray(detail?.steps) ? detail.steps : [];
  const executionIds: string[] = Array.isArray(detail?.active_execution_ids)
    ? detail.active_execution_ids
    : [];
  const note: string = typeof detail?.note === "string" ? detail.note : "";

  return (
    <Box sx={{ p: 2, bgcolor: "action.hover" }}>
      <Typography variant="subtitle2">Steps</Typography>
      <Table size="small">
        <TableBody>
          {steps.map((s: any, i: number) => {
            const evidence: any[] = Array.isArray(s.required_evidence)
              ? s.required_evidence
              : [];
            const order =
              typeof s.order === "number" ? s.order + 1 : i + 1;
            return (
              <TableRow key={s.step_id ?? i}>
                <TableCell width={40}>{order}</TableCell>
                <TableCell>{s.instruction ?? ""}</TableCell>
                <TableCell>
                  {evidence.map((ev: any, j: number) => (
                    <Chip
                      key={j}
                      size="small"
                      label={humanizeEnum(ev?.proof_type) || "Proof"}
                      color="warning"
                      sx={{ mr: 0.5 }}
                    />
                  ))}
                  {s.is_optional && (
                    <Chip size="small" label="optional" sx={{ ml: 0.5 }} />
                  )}
                </TableCell>
              </TableRow>
            );
          })}
          {steps.length === 0 && (
            <TableRow>
              <TableCell colSpan={3}>No steps defined.</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle2">Active executions</Typography>
      {executionIds.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {note || "None running."}
        </Typography>
      ) : (
        executionIds.map((execId) => (
          <Typography key={execId} variant="body2">
            {execId}
          </Typography>
        ))
      )}
    </Box>
  );
}

function CreateProcedureDialog(props: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const authority = useAuthority();
  const scopes = scopesForAuthority(authority);
  // Backend Scope enum values are lowercase; scopesForAuthority returns SCREAMING
  // labels. Map to enum values (goals-procedures-4: scope was sent UPPERCASE).
  // Key the memo on content so the seeding effect doesn't re-fire each render.
  const scopeKey = scopes.join(",");
  const scopeValues = useMemo(
    () => scopeKey.split(",").filter(Boolean).map((s) => s.toLowerCase()),
    [scopeKey],
  );
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // Do not hardcode a pre-permissions default (goals-procedures-11); seed from
  // the resolved scope list.
  const [scope, setScope] = useState("");
  const [domain, setDomain] = useState<string>(DECISION_DOMAINS[0]);
  const [enabled, setEnabled] = useState(true);
  const [steps, setSteps] = useState<StepDraft[]>([
    { instruction: "", proof_required: false, proof_type: "receipt", optional: false },
  ]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!scope || !scopeValues.includes(scope)) {
      setScope(scopeValues[0] ?? "");
    }
  }, [scopeValues, scope]);

  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      // Align the payload with ProcedureDefinition / ProcedureStep
      // (schemas/procedure.py) — goals-procedures-4:
      //  - scope: lowercase Scope enum value (handled via scopeValues above)
      //  - step_id: a real UUID (was "step-N")
      //  - order: required, 0-based
      //  - required_evidence: list[ProofRequirement] (was proof_required/proof_type)
      //  - is_optional (was "optional")
      // ProcedureDefinition also rejects procedures that are neither
      // auto-triggered nor manual-only, so dashboard-created procedures (which
      // carry no activation triggers) must set is_manual_only=true.
      await apiSend("POST", "/procedures/", {
        name,
        description,
        scope,
        decision_domain: domain,
        enabled,
        is_manual_only: true,
        steps: steps
          .filter((s) => s.instruction.trim())
          .map((s, i) => ({
            step_id: crypto.randomUUID(),
            order: i,
            instruction: s.instruction,
            is_optional: s.optional,
            required_evidence: s.proof_required
              ? [
                  {
                    description: s.instruction,
                    required: true,
                    proof_type: s.proof_type,
                  },
                ]
              : [],
          })),
      });
      props.onCreated();
      props.onClose();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="md">
      <DialogTitle>Create procedure</DialogTitle>
      <DialogContent>
        {err && <Alert severity="error">{err}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <Stack direction="row" spacing={2}>
            <TextField
              select
              label="Scope"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              sx={{ flex: 1 }}
            >
              {scopeValues.map((v) => (
                <MenuItem key={v} value={v}>
                  {humanizeEnum(v)}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Decision domain"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              sx={{ flex: 1 }}
            >
              {DECISION_DOMAINS.map((d) => (
                <MenuItem key={d} value={d}>
                  {humanizeEnum(d)}
                </MenuItem>
              ))}
            </TextField>
            <Stack direction="row" alignItems="center">
              <Typography variant="body2">Enabled</Typography>
              <Switch
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
            </Stack>
          </Stack>

          <Typography variant="subtitle2">Steps</Typography>
          {steps.map((s, i) => (
            <Paper key={i} variant="outlined" sx={{ p: 1.5 }}>
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <TextField
                    label={`Step ${i + 1} instruction`}
                    fullWidth
                    value={s.instruction}
                    onChange={(e) =>
                      updateStep(i, { instruction: e.target.value })
                    }
                  />
                  <IconButton
                    onClick={() =>
                      setSteps((prev) => prev.filter((_, j) => j !== i))
                    }
                  >
                    <DeleteIcon />
                  </IconButton>
                </Stack>
                <Stack direction="row" spacing={2} alignItems="center">
                  <Stack direction="row" alignItems="center">
                    <Typography variant="caption">Proof required</Typography>
                    <Switch
                      size="small"
                      checked={s.proof_required}
                      onChange={(e) =>
                        updateStep(i, { proof_required: e.target.checked })
                      }
                    />
                  </Stack>
                  {s.proof_required && (
                    <TextField
                      select
                      size="small"
                      label="Proof type"
                      value={s.proof_type}
                      onChange={(e) =>
                        updateStep(i, { proof_type: e.target.value })
                      }
                      sx={{ minWidth: 200 }}
                    >
                      {PROOF_TYPES.map((p) => (
                        <MenuItem key={p} value={p}>
                          {humanizeEnum(p)}
                        </MenuItem>
                      ))}
                    </TextField>
                  )}
                  <Stack direction="row" alignItems="center">
                    <Typography variant="caption">Optional</Typography>
                    <Switch
                      size="small"
                      checked={s.optional}
                      onChange={(e) =>
                        updateStep(i, { optional: e.target.checked })
                      }
                    />
                  </Stack>
                </Stack>
              </Stack>
            </Paper>
          ))}
          <Button
            startIcon={<AddIcon />}
            onClick={() =>
              setSteps((prev) => [
                ...prev,
                {
                  instruction: "",
                  proof_required: false,
                  proof_type: "receipt",
                  optional: false,
                },
              ])
            }
          >
            Add step
          </Button>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name || saving} onClick={submit}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export const ProceduresPage: React.FC = () => {
  const authority = useAuthority();
  const [rows, setRows] = useState<Procedure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/dashboard/procedures");
      setRows(Array.isArray(res) ? res : (res.items ?? res.procedures ?? []));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Client-side name/description filter + name sort (goals-procedures-12).
  const visibleRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? rows.filter(
          (p) =>
            (p.name || "").toLowerCase().includes(q) ||
            (p.description || "").toLowerCase().includes(q),
        )
      : rows;
    return [...filtered].sort((a, b) =>
      (a.name || "").localeCompare(b.name || ""),
    );
  }, [rows, query]);

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Procedures</Typography>
        {authority >= 50 && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            Create Procedure
          </Button>
        )}
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mb: 1 }}>
          {error}
        </Alert>
      )}
      {loading ? (
        <CircularProgress />
      ) : rows.length === 0 ? (
        <Box sx={{ py: 4, textAlign: "center" }}>
          <Typography color="text.secondary" gutterBottom>
            No procedures defined yet.
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {authority >= 50
              ? "Use “Create Procedure” to define a reusable, step-by-step runbook."
              : "Procedures appear here once a team lead or admin defines them."}
          </Typography>
        </Box>
      ) : (
        <>
          <TextField
            size="small"
            placeholder="Filter procedures by name or description…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{ mb: 1, minWidth: 320 }}
          />
          {visibleRows.length === 0 ? (
            <Typography color="text.secondary" sx={{ mt: 2 }}>
              No procedures match “{query.trim()}”.
            </Typography>
          ) : (
            <Paper variant="outlined">
              <Table>
                <TableHead>
                  <TableRow>
                    <TableCell />
                    <TableCell>Name</TableCell>
                    <TableCell>Scope</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {visibleRows.map((p) => {
                    const id = procId(p);
                    const isOpen = expanded === id;
                    return (
                      <React.Fragment key={id}>
                        <TableRow hover>
                          <TableCell>
                            <IconButton
                              size="small"
                              onClick={() => setExpanded(isOpen ? null : id)}
                            >
                              {isOpen ? (
                                <ExpandMoreIcon />
                              ) : (
                                <ChevronRightIcon />
                              )}
                            </IconButton>
                          </TableCell>
                          <TableCell>
                            <Typography variant="body2">{p.name}</Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {p.description?.slice(0, 80)}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            {p.scope && (
                              <Chip size="small" label={humanizeEnum(p.scope)} />
                            )}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell colSpan={3} sx={{ p: 0, border: 0 }}>
                            <Collapse in={isOpen} unmountOnExit>
                              {isOpen && <DetailPanel id={id} />}
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </Paper>
          )}
        </>
      )}
      <CreateProcedureDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={load}
      />
    </Box>
  );
};

export default ProceduresPage;
