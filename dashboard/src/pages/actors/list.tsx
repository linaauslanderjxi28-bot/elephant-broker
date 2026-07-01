// Actors list page.
//
// Enriched actor listing (fact count, last active) with type / org / status
// filters and a registration dialog. Visible to team leads+ (authority >= 50);
// registration requires authority >= 70.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  ListSubheader,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { DataGrid, type GridColDef } from "@mui/x-data-grid";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import {
  actorTypeColor,
  ACTOR_TYPE_GROUPS,
  apiGet,
  apiSend,
  authorityLabel,
  AUTHORITY_OPTIONS,
  relativeTime,
  useAuthority,
} from "../home/dashboardApi";

interface Actor {
  actor_id?: string;
  id?: string;
  eb_id?: string;
  display_name?: string;
  actor_type?: string;
  type?: string;
  authority_level?: number;
  org_id?: string;
  organization_name?: string;
  team_names?: string[];
  fact_count?: number;
  last_active?: string;
  active?: boolean;
}

function actorId(a: Actor): string {
  return String(a.actor_id ?? a.eb_id ?? a.id ?? "");
}

function RegisterActorDialog(props: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [type, setType] = useState("WORKER_AGENT");
  const [authorityLvl, setAuthorityLvl] = useState(0);
  const [handles, setHandles] = useState<string[]>([""]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      await apiSend("POST", "/admin/actors", {
        display_name: displayName,
        actor_type: type,
        authority_level: authorityLvl,
        handles: handles.filter((h) => h.includes(":")),
      });
      props.onCreated();
      props.onClose();
      setDisplayName("");
      setHandles([""]);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Register actor</DialogTitle>
      <DialogContent>
        {err && <Alert severity="error">{err}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Display name"
            required
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
          <TextField
            select
            label="Type"
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {Object.entries(ACTOR_TYPE_GROUPS).flatMap(([group, types]) => [
              <ListSubheader key={group}>{group}</ListSubheader>,
              ...types.map((t) => (
                <MenuItem key={t} value={t}>
                  {t}
                </MenuItem>
              )),
            ])}
          </TextField>
          <TextField
            select
            label="Authority level"
            value={authorityLvl}
            onChange={(e) => setAuthorityLvl(Number(e.target.value))}
          >
            {AUTHORITY_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value}>
                {o.label}
              </MenuItem>
            ))}
          </TextField>
          <Typography variant="subtitle2">Handles (platform:id)</Typography>
          {handles.map((h, i) => (
            <Stack key={i} direction="row" spacing={1}>
              <TextField
                fullWidth
                placeholder="email:alex@acme.com"
                value={h}
                onChange={(e) =>
                  setHandles((prev) =>
                    prev.map((v, j) => (j === i ? e.target.value : v)),
                  )
                }
              />
              <IconButton
                onClick={() =>
                  setHandles((prev) => prev.filter((_, j) => j !== i))
                }
              >
                <DeleteIcon />
              </IconButton>
            </Stack>
          ))}
          <Button
            startIcon={<AddIcon />}
            onClick={() => setHandles((prev) => [...prev, ""])}
          >
            Add handle
          </Button>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!displayName || saving}
          onClick={submit}
        >
          Register
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export const ActorsPage: React.FC = () => {
  const authority = useAuthority();
  const { push } = useNavigation();
  const [rows, setRows] = useState<Actor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"active" | "inactive" | "all">(
    "active",
  );
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/dashboard/actors");
      setRows(Array.isArray(res) ? res : (res.items ?? res.actors ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(
    () =>
      rows.filter((a) => {
        const t = a.actor_type ?? a.type;
        if (typeFilter && t !== typeFilter) return false;
        const isActive = a.active !== false;
        if (statusFilter === "active" && !isActive) return false;
        if (statusFilter === "inactive" && isActive) return false;
        return true;
      }),
    [rows, typeFilter, statusFilter],
  );

  // Columns use renderCell only (stable signature across x-data-grid v6/v7).
  const columns: GridColDef[] = [
    {
      field: "display_name",
      headerName: "Name",
      flex: 1,
      minWidth: 160,
      renderCell: (p) => <span>{p.row.display_name ?? ""}</span>,
    },
    {
      field: "actor_type",
      headerName: "Type",
      width: 170,
      renderCell: (p) => {
        const t = p.row.actor_type ?? p.row.type;
        return <Chip size="small" label={t ?? ""} color={actorTypeColor(t)} />;
      },
    },
    {
      field: "organization_name",
      headerName: "Organization",
      width: 150,
      renderCell: (p) => (
        <span>{p.row.organization_name ?? p.row.org_id ?? ""}</span>
      ),
    },
    {
      field: "authority_level",
      headerName: "Authority",
      width: 130,
      renderCell: (p) => (
        <Chip
          size="small"
          variant="outlined"
          label={authorityLabel(Number(p.row.authority_level ?? 0))}
        />
      ),
    },
    {
      field: "fact_count",
      headerName: "Facts",
      width: 90,
      renderCell: (p) => <span>{p.row.fact_count ?? 0}</span>,
    },
    {
      field: "last_active",
      headerName: "Last active",
      width: 140,
      renderCell: (p) => <span>{relativeTime(p.row.last_active)}</span>,
    },
    {
      field: "active",
      headerName: "Status",
      width: 100,
      renderCell: (p) =>
        p.row.active === false ? (
          <Chip size="small" label="inactive" />
        ) : (
          <Chip size="small" color="success" label="active" />
        ),
    },
  ];

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">Actors</Typography>
        {authority >= 70 && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            Register Actor
          </Button>
        )}
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <TextField
          select
          size="small"
          label="Type"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="">All types</MenuItem>
          {Object.entries(ACTOR_TYPE_GROUPS).flatMap(([group, types]) => [
            <ListSubheader key={group}>{group}</ListSubheader>,
            ...types.map((t) => (
              <MenuItem key={t} value={t}>
                {t}
              </MenuItem>
            )),
          ])}
        </TextField>
        <TextField
          select
          size="small"
          label="Status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as any)}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="active">Active</MenuItem>
          <MenuItem value="inactive">Inactive</MenuItem>
          <MenuItem value="all">All</MenuItem>
        </TextField>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}
      <div style={{ width: "100%" }}>
        <DataGrid
          autoHeight
          loading={loading}
          rows={filtered.map((a) => ({ id: actorId(a), ...a }))}
          columns={columns}
          onRowClick={(p) => push(`/actors/${p.id}`)}
          pageSizeOptions={[25, 50, 100]}
          initialState={{
            pagination: { paginationModel: { pageSize: 25, page: 0 } },
          }}
        />
      </div>

      <RegisterActorDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={load}
      />
    </Box>
  );
};

export default ActorsPage;
