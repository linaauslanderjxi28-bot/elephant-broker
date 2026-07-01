// Procedures page.
//
// Lists procedure definitions with an expandable detail panel (steps, active
// executions, recent completions) and a creation form with full step editor.

import React, { useCallback, useEffect, useState } from "react";
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
  LinearProgress,
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
  relativeTime,
  scopesForAuthority,
  useAuthority,
} from "../home/dashboardApi";

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
  enabled?: boolean;
  steps?: any[];
  active_executions?: any[];
  recent_completions?: any[];
  total_completions?: number;
}

const PROOF_TYPES = [
  "DIFF_HASH",
  "CHUNK_REF",
  "RECEIPT",
  "VERSION_RECORD",
  "SUPERVISOR_SIGN_OFF",
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
  const steps = detail?.steps ?? detail?.definition?.steps ?? [];
  const executions = detail?.active_executions ?? [];
  const completions = detail?.recent_completions ?? [];

  return (
    <Box sx={{ p: 2, bgcolor: "action.hover" }}>
      <Typography variant="subtitle2">Steps</Typography>
      <Table size="small">
        <TableBody>
          {steps.map((s: any, i: number) => (
            <TableRow key={i}>
              <TableCell width={40}>{i + 1}</TableCell>
              <TableCell>{s.instruction ?? s.text ?? ""}</TableCell>
              <TableCell>
                {s.proof_required && (
                  <Chip
                    size="small"
                    label={s.proof_type || "proof"}
                    color="warning"
                  />
                )}
                {s.optional && (
                  <Chip size="small" label="optional" sx={{ ml: 0.5 }} />
                )}
              </TableCell>
            </TableRow>
          ))}
          {steps.length === 0 && (
            <TableRow>
              <TableCell colSpan={3}>No steps defined.</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle2">Active executions</Typography>
      {executions.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          None running.
        </Typography>
      ) : (
        executions.map((ex: any, i: number) => {
          const total = ex.total_steps ?? steps.length ?? 1;
          const done = ex.completed_steps ?? 0;
          return (
            <Box key={i} sx={{ my: 1 }}>
              <Typography variant="body2">
                {ex.actor_id ?? "actor"} · {ex.session_key ?? ""} · step{" "}
                {done}/{total}
              </Typography>
              <LinearProgress
                variant="determinate"
                value={total ? (done / total) * 100 : 0}
              />
            </Box>
          );
        })
      )}

      <Divider sx={{ my: 1 }} />
      <Typography variant="subtitle2">Recent completions</Typography>
      {completions.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No history.
        </Typography>
      ) : (
        completions.slice(0, 10).map((c: any, i: number) => (
          <Typography key={i} variant="body2">
            {relativeTime(c.timestamp ?? c.completed_at)} · {c.actor_id ?? ""}
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
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState(scopes[0] || "ACTOR");
  const [domain, setDomain] = useState<string>(DECISION_DOMAINS[0]);
  const [enabled, setEnabled] = useState(true);
  const [steps, setSteps] = useState<StepDraft[]>([
    { instruction: "", proof_required: false, proof_type: "RECEIPT", optional: false },
  ]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await apiSend("POST", "/procedures/", {
        name,
        description,
        scope,
        decision_domain: domain,
        enabled,
        steps: steps
          .filter((s) => s.instruction.trim())
          .map((s, i) => ({
            step_id: `step-${i + 1}`,
            instruction: s.instruction,
            proof_required: s.proof_required,
            proof_type: s.proof_required ? s.proof_type : null,
            optional: s.optional,
          })),
      });
      props.onCreated();
      props.onClose();
    } catch (e) {
      setErr((e as Error).message);
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
              {scopes.map((s) => (
                <MenuItem key={s} value={s}>
                  {s}
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
                  {d}
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
                          {p}
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
                  proof_type: "RECEIPT",
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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/dashboard/procedures");
      setRows(Array.isArray(res) ? res : (res.items ?? res.procedures ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleEnabled = async (p: Procedure) => {
    await apiSend("PUT", `/procedures/${procId(p)}`, {
      enabled: !p.enabled,
    });
    void load();
  };

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
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell />
                <TableCell>Name</TableCell>
                <TableCell>Scope</TableCell>
                <TableCell align="right">Steps</TableCell>
                <TableCell align="right">Active</TableCell>
                <TableCell align="right">Completions</TableCell>
                <TableCell>Enabled</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((p) => {
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
                          {isOpen ? <ExpandMoreIcon /> : <ChevronRightIcon />}
                        </IconButton>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">{p.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {p.description?.slice(0, 80)}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {p.scope && <Chip size="small" label={p.scope} />}
                      </TableCell>
                      <TableCell align="right">
                        {p.steps?.length ?? "—"}
                      </TableCell>
                      <TableCell align="right">
                        {p.active_executions?.length ?? 0}
                      </TableCell>
                      <TableCell align="right">
                        {p.total_completions ?? 0}
                      </TableCell>
                      <TableCell>
                        <Switch
                          size="small"
                          checked={!!p.enabled}
                          disabled={authority < 50}
                          onChange={() => toggleEnabled(p)}
                        />
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={7} sx={{ p: 0, border: 0 }}>
                        <Collapse in={isOpen} unmountOnExit>
                          {isOpen && <DetailPanel id={id} />}
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </React.Fragment>
                );
              })}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7}>No procedures defined.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
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
