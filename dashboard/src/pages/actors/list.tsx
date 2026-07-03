// Actors list page.
//
// Enriched actor listing (fact count, last active) with type / org / status
// filters and a registration dialog. Visible to team leads+ (authority >= 50);
// registration and field edits require authority >= 70, merging requires >= 90.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  ListSubheader,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { DataGrid, type GridColDef } from "@mui/x-data-grid";
import AddIcon from "@mui/icons-material/Add";
import CallMergeIcon from "@mui/icons-material/CallMerge";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
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
import { humanizeEnum } from "../../lib/format";
import { actorDisplayName } from "../../lib/labels";
import { errorMessage } from "../../lib/errors";

export interface Actor {
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
  handles?: string[];
  fact_count?: number;
  last_active?: string;
  active?: boolean;
}

export function actorId(a: Actor): string {
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
      // Backend contract (admin.py register_actor): key is `type`, values are
      // lowercase ActorType members (e.g. "worker_agent").
      await apiSend("POST", "/admin/actors", {
        display_name: displayName,
        type: type.toLowerCase(),
        authority_level: authorityLvl,
        handles: handles.filter((h) => h.includes(":")),
      });
      props.onCreated();
      props.onClose();
      setDisplayName("");
      setHandles([""]);
    } catch (e) {
      setErr(errorMessage(e));
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
                  {humanizeEnum(t)}
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

/**
 * Edit dialog for an existing actor. Wired to PUT /admin/actors/{actor_id},
 * which applies display_name, authority_level and handles. The actor type is
 * shown read-only: the update endpoint does not apply type changes.
 * Backend authority: `register_actor` (>= 70).
 */
export function EditActorDialog(props: {
  open: boolean;
  actor: Actor | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [authorityLvl, setAuthorityLvl] = useState(0);
  const [handles, setHandles] = useState<string[]>([""]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (props.open && props.actor) {
      setDisplayName(props.actor.display_name ?? "");
      setAuthorityLvl(Number(props.actor.authority_level ?? 0));
      setHandles(
        props.actor.handles && props.actor.handles.length > 0
          ? [...props.actor.handles]
          : [""],
      );
      setErr(null);
    }
  }, [props.open, props.actor]);

  const cleanedHandles = handles.map((h) => h.trim()).filter((h) => h !== "");
  const invalidHandles = cleanedHandles.filter((h) => !h.includes(":"));

  // Preserve non-standard levels (e.g. 60) by adding them as an extra option.
  const authorityOptions = AUTHORITY_OPTIONS.some(
    (o) => o.value === authorityLvl,
  )
    ? AUTHORITY_OPTIONS
    : [
        ...AUTHORITY_OPTIONS,
        { label: `Custom (${authorityLvl})`, value: authorityLvl },
      ].sort((a, b) => a.value - b.value);

  const submit = async () => {
    if (!props.actor) return;
    setSaving(true);
    setErr(null);
    try {
      await apiSend("PUT", `/admin/actors/${actorId(props.actor)}`, {
        display_name: displayName.trim(),
        authority_level: authorityLvl,
        handles: cleanedHandles,
      });
      props.onSaved();
      props.onClose();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Edit actor</DialogTitle>
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
            label="Type"
            value={props.actor?.actor_type ?? props.actor?.type ?? ""}
            disabled
            helperText="Actor type cannot be changed after registration."
          />
          <TextField
            select
            label="Authority level"
            value={authorityLvl}
            onChange={(e) => setAuthorityLvl(Number(e.target.value))}
          >
            {authorityOptions.map((o) => (
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
                error={h.trim() !== "" && !h.includes(":")}
                helperText={
                  h.trim() !== "" && !h.includes(":")
                    ? "Handles must use the platform:id format"
                    : undefined
                }
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
          disabled={
            !displayName.trim() || invalidHandles.length > 0 || saving
          }
          onClick={submit}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/**
 * Two-actor merge picker + confirmation. Wired to
 * POST /admin/actors/{target}/merge with body { duplicate_id: source } —
 * the source (duplicate) is absorbed into the target (survivor).
 * UI-gated at authority >= 90 per SOW (backend `merge_actors` rule
 * defaults to >= 70 and is configurable).
 */
export function MergeActorsDialog(props: {
  open: boolean;
  actors: Actor[];
  onClose: () => void;
  onMerged: () => void;
}) {
  const [source, setSource] = useState<Actor | null>(null); // duplicate
  const [target, setTarget] = useState<Actor | null>(null); // survivor
  const [confirming, setConfirming] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);

  useEffect(() => {
    if (props.open) {
      setSource(null);
      setTarget(null);
      setConfirming(false);
      setErr(null);
    }
  }, [props.open]);

  const label = (a: Actor) =>
    `${actorDisplayName(a.display_name) || "(unnamed)"} — ${authorityLabel(
      Number(a.authority_level ?? 0),
    )} · ${actorId(a).slice(0, 8)}`;

  const submit = async () => {
    if (!source || !target) return;
    setMerging(true);
    setErr(null);
    try {
      await apiSend("POST", `/admin/actors/${actorId(target)}/merge`, {
        duplicate_id: actorId(source),
      });
      props.onMerged();
      props.onClose();
    } catch (e) {
      const raw = (e as Error).message ?? "";
      setErr(
        raw.startsWith("501")
          ? "Actor merge is not implemented by this server build (HTTP 501)."
          : errorMessage(e),
      );
    } finally {
      setMerging(false);
    }
  };

  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>Merge actors</DialogTitle>
      <DialogContent>
        {err && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {err}
          </Alert>
        )}
        {!confirming ? (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              Pick the duplicate actor to merge away, and the surviving actor
              that absorbs it.
            </Typography>
            <Autocomplete
              options={props.actors.filter(
                (a) => !target || actorId(a) !== actorId(target),
              )}
              value={source}
              onChange={(_, v) => setSource(v)}
              getOptionLabel={label}
              isOptionEqualToValue={(o, v) => actorId(o) === actorId(v)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Duplicate (merged away)"
                  placeholder="Search actors…"
                />
              )}
            />
            <Autocomplete
              options={props.actors.filter(
                (a) => !source || actorId(a) !== actorId(source),
              )}
              value={target}
              onChange={(_, v) => setTarget(v)}
              getOptionLabel={label}
              isOptionEqualToValue={(o, v) => actorId(o) === actorId(v)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Survivor (keeps identity)"
                  placeholder="Search actors…"
                />
              )}
            />
          </Stack>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Alert severity="warning">
              This action cannot be undone.
            </Alert>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip
                size="small"
                label={actorDisplayName(source?.display_name) || "(unnamed)"}
              />
              <CallMergeIcon fontSize="small" color="action" />
              <Chip
                size="small"
                color="primary"
                label={actorDisplayName(target?.display_name) || "(unnamed)"}
              />
            </Stack>
            <Typography variant="body2">
              Merging marks{" "}
              <strong>
                {actorDisplayName(source?.display_name) || "the duplicate"}
              </strong>{" "}
              as a duplicate of{" "}
              <strong>
                {actorDisplayName(target?.display_name) || "the survivor"}
              </strong>
              :
            </Typography>
            <Typography variant="body2" component="ul" sx={{ m: 0, pl: 3 }}>
              <li>
                The duplicate&apos;s identity (handles, relationships, memory
                provenance) is consolidated onto the survivor.
              </li>
              <li>
                The duplicate no longer appears as a separate actor.
              </li>
              <li>
                The survivor keeps its own display name, type and authority
                level.
              </li>
            </Typography>
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {!confirming ? (
          <>
            <Button onClick={props.onClose}>Cancel</Button>
            <Button
              variant="contained"
              disabled={
                !source || !target || actorId(source) === actorId(target)
              }
              onClick={() => setConfirming(true)}
            >
              Continue
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => setConfirming(false)} disabled={merging}>
              Back
            </Button>
            <Button
              variant="contained"
              color="error"
              disabled={merging}
              onClick={submit}
            >
              Confirm merge
            </Button>
          </>
        )}
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
  const [showInactive, setShowInactive] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [editActor, setEditActor] = useState<Actor | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Server-side active filtering: soft-deactivated actors (merged
      // duplicates, offboarded operators) are hidden unless opted in via the
      // "Show inactive" toggle. The backend route's tri-state `status` param
      // carries the toggle (`all` includes inactive, `active` hides them).
      const res = await apiGet<any>("/dashboard/actors", {
        status: showInactive ? "all" : "active",
      });
      setRows(Array.isArray(res) ? res : (res.items ?? res.actors ?? []));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [showInactive]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(
    () =>
      rows.filter((a) => {
        // Stored actor_type is lowercase ("worker_agent"); filter options are
        // uppercase constants — compare case-insensitively.
        const t = a.actor_type ?? a.type;
        if (typeFilter && (t ?? "").toUpperCase() !== typeFilter.toUpperCase())
          return false;
        return true;
      }),
    [rows, typeFilter],
  );

  // Columns use renderCell only (stable signature across x-data-grid v6/v7).
  const columns: GridColDef[] = [
    {
      field: "display_name",
      headerName: "Name",
      flex: 1,
      minWidth: 160,
      renderCell: (p) => (
        <span title={p.row.display_name ?? ""}>
          {actorDisplayName(p.row.display_name)}
        </span>
      ),
    },
    {
      field: "actor_type",
      headerName: "Type",
      width: 170,
      renderCell: (p) => {
        const t = p.row.actor_type ?? p.row.type;
        return (
          <Chip
            size="small"
            label={humanizeEnum(t) || "—"}
            color={actorTypeColor(t)}
          />
        );
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
    ...(authority >= 70
      ? ([
          {
            field: "actions",
            headerName: "",
            width: 70,
            sortable: false,
            filterable: false,
            renderCell: (p) => (
              <Tooltip title="Edit actor">
                <IconButton
                  size="small"
                  aria-label="Edit actor"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditActor(p.row as Actor);
                  }}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            ),
          },
        ] as GridColDef[])
      : []),
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
        <Stack direction="row" spacing={1}>
          {authority >= 90 && (
            <Button
              variant="outlined"
              startIcon={<CallMergeIcon />}
              onClick={() => setMergeOpen(true)}
            >
              Merge Actors
            </Button>
          )}
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
                {humanizeEnum(t)}
              </MenuItem>
            )),
          ])}
        </TextField>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
            />
          }
          label="Show inactive"
        />
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
      <EditActorDialog
        open={editActor !== null}
        actor={editActor}
        onClose={() => setEditActor(null)}
        onSaved={load}
      />
      <MergeActorsDialog
        open={mergeOpen}
        actors={rows}
        onClose={() => setMergeOpen(false)}
        onMerged={load}
      />
    </Box>
  );
};

export default ActorsPage;
