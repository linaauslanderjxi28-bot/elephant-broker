// Profiles page.
//
// Lists profiles with active-session counts and inheritance. Expanding a profile
// resolves its full policy (GET /profiles/{id}/resolve) rendered in
// non-technical sections plus a raw-parameter accordion. Compare mode diffs two
// resolved profiles side by side.

import React, { useCallback, useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { apiGet } from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";
import { humanizeEnum } from "../../lib/format";

// GET /dashboard/profiles -> { profiles: [{ profile_id, session_count }] }
// (schemas/dashboard.py::ProfileSummary). The page previously read `name`/`id`
// — neither of which the endpoint returns — so every row rendered blank and the
// resolve calls hit `/profiles//resolve` (guards-profiles-1 / cross-cutting-2).
// The human name + inheritance parent (`name`, `extends`) come from the resolve
// endpoint's policy, surfaced inside ResolvedSection.
interface Profile {
  profile_id: string;
  session_count?: number;
  // Tolerated legacy/alternate shapes so a bare array or `{name}` still renders.
  name?: string;
  id?: string;
}

function profileId(p: Profile): string {
  return String(p.profile_id ?? p.id ?? p.name ?? "");
}

function flatten(obj: any, prefix = ""): Record<string, any> {
  const out: Record<string, any> = {};
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v)) {
        Object.assign(out, flatten(v, key));
      } else {
        out[key] = v;
      }
    }
  }
  return out;
}

function ResolvedSection({ name }: { name: string }) {
  const [resolved, setResolved] = useState<any>(null);
  useEffect(() => {
    apiGet<any>(`/profiles/${name}/resolve`)
      .then(setResolved)
      .catch(() => setResolved({}));
  }, [name]);

  if (!resolved) return <CircularProgress size={20} />;
  const flat = flatten(resolved);
  // resolve -> { policy: { id, name, extends, ... }, weights } — the human name
  // and inheritance parent only exist here, not in the list row.
  const policy = resolved?.policy ?? resolved ?? {};
  const displayName: string = policy.name || humanizeEnum(policy.id) || "";
  const extendsFrom: string = policy.extends || "";

  return (
    <Box>
      {(displayName || extendsFrom) && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mb: 1, flexWrap: "wrap", rowGap: 1 }}
        >
          {displayName && <Typography variant="subtitle2">{displayName}</Typography>}
          {extendsFrom && (
            <Chip size="small" label={`inherits ${humanizeEnum(extendsFrom)}`} />
          )}
        </Stack>
      )}
      <Accordion defaultExpanded>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography>Raw parameters</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Table size="small">
            <TableBody>
              {Object.entries(flat).map(([k, v]) => (
                <TableRow key={k}>
                  <TableCell>{k}</TableCell>
                  <TableCell align="right">{String(v)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

function CompareDialog(props: {
  open: boolean;
  onClose: () => void;
  a: string;
  b: string;
}) {
  const [da, setDa] = useState<any>(null);
  const [db, setDb] = useState<any>(null);
  useEffect(() => {
    if (!props.open) return;
    apiGet(`/profiles/${props.a}/resolve`).then(setDa).catch(() => setDa({}));
    apiGet(`/profiles/${props.b}/resolve`).then(setDb).catch(() => setDb({}));
  }, [props.open, props.a, props.b]);

  const fa = flatten(da ?? {});
  const fb = flatten(db ?? {});
  const keys = Array.from(new Set([...Object.keys(fa), ...Object.keys(fb)]))
    .filter((k) => String(fa[k]) !== String(fb[k]))
    .sort();

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="md">
      <DialogTitle>
        Compare: {props.a} vs {props.b}
      </DialogTitle>
      <DialogContent>
        {!da || !db ? (
          <CircularProgress />
        ) : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Parameter</TableCell>
                <TableCell>{props.a}</TableCell>
                <TableCell>{props.b}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {keys.map((k) => (
                <TableRow key={k}>
                  <TableCell>{k}</TableCell>
                  <TableCell>{String(fa[k] ?? "—")}</TableCell>
                  <TableCell>{String(fb[k] ?? "—")}</TableCell>
                </TableRow>
              ))}
              {keys.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3}>No differences.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </DialogContent>
    </Dialog>
  );
}

export const ProfilesPage: React.FC = () => {
  const [rows, setRows] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/dashboard/profiles");
      setRows(Array.isArray(res) ? res : (res.items ?? res.profiles ?? []));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const toggleSelect = (name: string) =>
    setSelected((prev) =>
      prev.includes(name)
        ? prev.filter((n) => n !== name)
        : [...prev, name].slice(-2),
    );

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Profiles</Typography>
        <Button
          variant="outlined"
          disabled={selected.length !== 2}
          onClick={() => setCompareOpen(true)}
        >
          Compare
        </Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress />
      ) : (
        <Paper variant="outlined">
          {rows.map((p) => {
            const id = profileId(p);
            return (
              <Accordion key={id}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Checkbox
                    checked={selected.includes(id)}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleSelect(id);
                    }}
                  />
                  <Stack sx={{ flex: 1 }}>
                    <Typography>{humanizeEnum(id) || id}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {id}
                    </Typography>
                  </Stack>
                  {/* Inheritance parent, real session_count, and org-override
                      flags are not in the ProfileSummary the list endpoint
                      returns (guards-profiles-8) — inheritance is surfaced from
                      the resolve endpoint inside ResolvedSection; the rest are
                      reported as a backend crossFileNeed rather than faked. */}
                </AccordionSummary>
                <AccordionDetails>
                  <ResolvedSection name={id} />
                </AccordionDetails>
              </Accordion>
            );
          })}
          {rows.length === 0 && (
            <Typography sx={{ p: 2 }} color="text.secondary">
              No profiles.
            </Typography>
          )}
        </Paper>
      )}
      {selected.length === 2 && (
        <CompareDialog
          open={compareOpen}
          onClose={() => setCompareOpen(false)}
          a={selected[0]}
          b={selected[1]}
        />
      )}
    </Box>
  );
};

export default ProfilesPage;
