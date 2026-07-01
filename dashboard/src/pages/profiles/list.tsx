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

interface Profile {
  name?: string;
  id?: string;
  inherits_from?: string;
  description?: string;
  active_sessions?: number;
  session_count?: number;
  has_org_override?: boolean;
}

function profileName(p: Profile): string {
  return String(p.name ?? p.id ?? "");
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

  return (
    <Box>
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
      setError((e as Error).message);
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
            const name = profileName(p);
            return (
              <Accordion key={name}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Checkbox
                    checked={selected.includes(name)}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleSelect(name);
                    }}
                  />
                  <Stack sx={{ flex: 1 }}>
                    <Typography>{name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {p.description}
                    </Typography>
                  </Stack>
                  {p.inherits_from && (
                    <Chip
                      size="small"
                      label={`inherits ${p.inherits_from}`}
                      sx={{ mr: 1 }}
                    />
                  )}
                  <Chip
                    size="small"
                    label={`${p.active_sessions ?? p.session_count ?? 0} sessions`}
                  />
                  {p.has_org_override && (
                    <Chip
                      size="small"
                      color="secondary"
                      label="custom"
                      sx={{ ml: 1 }}
                    />
                  )}
                </AccordionSummary>
                <AccordionDetails>
                  <ResolvedSection name={name} />
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
