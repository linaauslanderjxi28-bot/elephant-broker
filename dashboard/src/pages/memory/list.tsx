// Memory Browse (`/memory`) — the primary dashboard surface.
//
// A searchable, filterable, server-paginated table of everything the system
// remembers. Backend: `POST /dashboard/memory/browse` -> PaginatedResult
// (routed through the Refine dataProvider `memory` resource). Row + bulk
// mutations hit the existing runtime `/memory/{id}` endpoints directly via
// custom mutations so they are independent of the resource's list mapping.
//
// Implements plan Section 2 "Memory Browse" + SOW page 2.

import { useCallback, useEffect, useMemo, useRef, useState, type FC } from "react";
import { useNavigate } from "react-router";
import { useSearchParams } from "react-router-dom";
import {
  useApiUrl,
  useCustom,
  useCustomMutation,
  useNotification,
  usePermissions,
  type CrudFilters,
} from "@refinedev/core";
import { List, useDataGrid } from "@refinedev/mui";
import {
  DataGrid,
  GridActionsCellItem,
  GridToolbarColumnsButton,
  GridToolbarContainer,
  GridToolbarDensitySelector,
  GridToolbarExport,
  type GridColDef,
  type GridRowSelectionModel,
} from "@mui/x-data-grid";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Slider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import VerifiedIcon from "@mui/icons-material/VerifiedUser";
import BookmarkIcon from "@mui/icons-material/BookmarkBorder";
import { formatDistanceToNow } from "date-fns";

import {
  AUTH_DELETE,
  AUTH_EDIT,
  CATEGORY_LABELS,
  CATEGORY_OPTIONS,
  MEMORY_CLASS_COLORS,
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  MEMORY_CLASS_OPTIONS,
  SCOPE_LABELS,
  SCOPE_OPTIONS,
  factClassLabel,
  scopeLabel,
  type FactAssertion,
  type MemoryClass,
  type Scope,
} from "./types";

interface FilterState {
  scope: Scope | "";
  memoryClass: MemoryClass | "";
  category: string;
  minConfidence: number;
  text: string;
  goalId: string;
  sourceActorId: string;
}

const EMPTY_FILTERS: FilterState = {
  scope: "",
  memoryClass: "",
  category: "",
  minConfidence: 0,
  text: "",
  goalId: "",
  sourceActorId: "",
};

// Translate a FilterState into the CrudFilters the dataProvider maps onto the
// `POST /dashboard/memory/browse` request body. Field/operator pairs must
// survive the dataProvider's `flattenFilters` (which suffixes `_gte` /
// `_contains`) and land on the names `buildMemoryBrowseBody` reads:
//   confidence + gte  -> confidence_gte -> body.min_confidence
//   text + contains   -> text_contains  -> body.text_contains
function buildCrudFilters(next: FilterState): CrudFilters {
  const crud: CrudFilters = [];
  if (next.scope) crud.push({ field: "scope", operator: "eq", value: next.scope });
  if (next.memoryClass)
    crud.push({ field: "memory_class", operator: "eq", value: next.memoryClass });
  if (next.category) crud.push({ field: "category", operator: "eq", value: next.category });
  if (next.minConfidence > 0)
    crud.push({ field: "confidence", operator: "gte", value: next.minConfidence });
  if (next.text.trim())
    crud.push({ field: "text", operator: "contains", value: next.text.trim() });
  if (next.goalId) crud.push({ field: "goal_id", operator: "eq", value: next.goalId });
  if (next.sourceActorId)
    crud.push({ field: "source_actor_id", operator: "eq", value: next.sourceActorId });
  return crud;
}

// Deep links from other pages land here with URL search params, e.g.
// `/memory?goal_id=…` (Goals "Related facts") and
// `/memory?source_actor_id=…` (Actor "View all facts"). Parse the params the
// browse endpoint understands into a partial FilterState.
function parseFiltersFromSearch(params: URLSearchParams): Partial<FilterState> {
  const out: Partial<FilterState> = {};
  const scope = params.get("scope");
  if (scope && (SCOPE_OPTIONS as string[]).includes(scope)) out.scope = scope as Scope;
  const memoryClass = params.get("memory_class");
  if (memoryClass && (MEMORY_CLASS_OPTIONS as string[]).includes(memoryClass))
    out.memoryClass = memoryClass as MemoryClass;
  const category = params.get("category");
  if (category && CATEGORY_OPTIONS.includes(category)) out.category = category;
  const minConfidence = params.get("min_confidence");
  if (minConfidence !== null) {
    const v = Number(minConfidence);
    if (Number.isFinite(v) && v > 0) out.minConfidence = Math.min(1, v);
  }
  const text = params.get("text") ?? params.get("text_contains");
  if (text) out.text = text;
  const goalId = params.get("goal_id");
  if (goalId) out.goalId = goalId;
  const sourceActorId = params.get("source_actor_id");
  if (sourceActorId) out.sourceActorId = sourceActorId;
  return out;
}

interface SavedView {
  id: string;
  name: string;
  resource: string;
  filters: FilterState;
  sort?: { field: string; order: "asc" | "desc" };
}

function relativeAge(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

function ClassChip({ value }: { value: string }) {
  const cls = value as MemoryClass;
  const hex = MEMORY_CLASS_HEX[cls];
  return (
    <Chip
      size="small"
      label={MEMORY_CLASS_LABELS[cls] ?? value}
      color={hex ? "default" : MEMORY_CLASS_COLORS[cls] ?? "default"}
      sx={hex ? { bgcolor: hex, color: "#fff" } : undefined}
    />
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <Tooltip title={`${pct}%`}>
      <Box sx={{ display: "flex", alignItems: "center", width: "100%", gap: 1 }}>
        <LinearProgress
          variant="determinate"
          value={pct}
          sx={{ flex: 1, height: 8, borderRadius: 1 }}
        />
        <Typography variant="caption" sx={{ minWidth: 32 }}>
          {pct}%
        </Typography>
      </Box>
    </Tooltip>
  );
}

export const MemoryList: FC = () => {
  const navigate = useNavigate();
  const apiUrl = useApiUrl();
  const { open } = useNotification();
  const { data: permissions } = usePermissions<{ authorityLevel?: number }>();
  const authorityLevel = permissions?.authorityLevel ?? 0;
  const canEdit = authorityLevel >= AUTH_EDIT;
  const canDelete = authorityLevel >= AUTH_DELETE;

  // Deep links (e.g. `/memory?goal_id=…`, `/memory?source_actor_id=…`) seed
  // the initial filter state; afterwards filtering stays purely local.
  const [searchParams] = useSearchParams();
  const initialFilters = useMemo<FilterState>(
    () => ({ ...EMPTY_FILTERS, ...parseFiltersFromSearch(searchParams) }),
    // Parse once on mount — later param changes are handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const { dataGridProps, setFilters, setSorters, tableQueryResult } =
    useDataGrid<FactAssertion>({
      resource: "memory",
      pagination: { pageSize: 50, mode: "server" },
      sorters: { mode: "server", initial: [{ field: "created_at", order: "desc" }] },
      filters: { mode: "server", initial: buildCrudFilters(initialFilters) },
      syncWithLocation: false,
    });

  const [filters, setLocalFilters] = useState<FilterState>(initialFilters);
  const [selection, setSelection] = useState<GridRowSelectionModel>([]);

  // x-data-grid changed the selection model shape across major versions
  // (array of ids vs `{ type, ids: Set }`). Normalize to a string[] so bulk
  // actions work regardless of the installed version.
  const selectedIds = useMemo<string[]>(() => {
    const sel = selection as unknown;
    if (Array.isArray(sel)) return sel.map((s) => String(s));
    const ids = (sel as { ids?: Iterable<unknown> } | null)?.ids;
    if (ids) return Array.from(ids).map((s) => String(s));
    return [];
  }, [selection]);

  // --- Filter application ------------------------------------------------
  const applyFilters = useCallback(
    (next: FilterState) => {
      setFilters(buildCrudFilters(next), "replace");
    },
    [setFilters],
  );

  // If the URL search params change while this page is already mounted
  // (e.g. an in-app navigation to `/memory?goal_id=…`), merge them in.
  const appliedSearchRef = useRef(searchParams.toString());
  useEffect(() => {
    const search = searchParams.toString();
    if (search === appliedSearchRef.current) return;
    appliedSearchRef.current = search;
    const parsed = parseFiltersFromSearch(searchParams);
    if (Object.keys(parsed).length === 0) return;
    setLocalFilters((prev) => {
      const next = { ...prev, ...parsed };
      applyFilters(next);
      return next;
    });
  }, [searchParams, applyFilters]);

  const updateFilter = useCallback(
    (patch: Partial<FilterState>) => {
      setLocalFilters((prev) => {
        const next = { ...prev, ...patch };
        applyFilters(next);
        return next;
      });
    },
    [applyFilters],
  );

  const clearFilters = useCallback(() => {
    setLocalFilters(EMPTY_FILTERS);
    setFilters([], "replace");
  }, [setFilters]);

  const activeFilterChips = useMemo(() => {
    const chips: { key: string; label: string; clear: () => void }[] = [];
    if (filters.scope)
      chips.push({
        key: "scope",
        label: `Scope: ${scopeLabel(filters.scope)}`,
        clear: () => updateFilter({ scope: "" }),
      });
    if (filters.memoryClass)
      chips.push({
        key: "class",
        label: `Type: ${factClassLabel(filters.memoryClass)}`,
        clear: () => updateFilter({ memoryClass: "" }),
      });
    if (filters.category)
      chips.push({
        key: "category",
        label: `Category: ${CATEGORY_LABELS[filters.category] ?? filters.category}`,
        clear: () => updateFilter({ category: "" }),
      });
    if (filters.minConfidence > 0)
      chips.push({
        key: "conf",
        label: `Min confidence: ${filters.minConfidence.toFixed(2)}`,
        clear: () => updateFilter({ minConfidence: 0 }),
      });
    if (filters.text.trim())
      chips.push({
        key: "text",
        label: `Contains: "${filters.text.trim()}"`,
        clear: () => updateFilter({ text: "" }),
      });
    if (filters.goalId)
      chips.push({
        key: "goal",
        label: `Goal: ${filters.goalId.slice(0, 8)}…`,
        clear: () => updateFilter({ goalId: "" }),
      });
    if (filters.sourceActorId)
      chips.push({
        key: "sourceActor",
        label: `Source actor: ${filters.sourceActorId.slice(0, 8)}…`,
        clear: () => updateFilter({ sourceActorId: "" }),
      });
    return chips;
  }, [filters, updateFilter]);

  // --- Mutations (direct runtime endpoints) ------------------------------
  const { mutate: customMutate } = useCustomMutation();
  const refetch = useCallback(() => tableQueryResult?.refetch?.(), [tableQueryResult]);

  const [deleteTarget, setDeleteTarget] = useState<string[] | null>(null);
  const [promoteState, setPromoteState] = useState<{
    ids: string[];
    kind: "scope" | "class";
    value: string;
  } | null>(null);

  const notifyOk = (message: string) =>
    open?.({ type: "success", message, key: `mem-${Date.now()}` });
  const notifyErr = (message: string) =>
    open?.({ type: "error", message, key: `mem-err-${Date.now()}` });

  const verifyFact = useCallback(
    (id: string) => {
      customMutate(
        {
          url: `${apiUrl}/memory/${id}`,
          method: "patch",
          values: { confidence: 1.0 },
        },
        {
          onSuccess: () => {
            notifyOk("Fact marked verified");
            refetch();
          },
          onError: () => notifyErr("Verify failed"),
        },
      );
    },
    [apiUrl, customMutate, refetch],
  );

  const doDelete = useCallback(
    (ids: string[]) => {
      let remaining = ids.length;
      ids.forEach((id) => {
        customMutate(
          { url: `${apiUrl}/memory/${id}`, method: "delete", values: {} },
          {
            onSuccess: () => {
              remaining -= 1;
              if (remaining <= 0) {
                notifyOk(`Deleted ${ids.length} fact(s)`);
                setSelection([]);
                refetch();
              }
            },
            onError: () => notifyErr("Delete failed"),
          },
        );
      });
      setDeleteTarget(null);
    },
    [apiUrl, customMutate, refetch],
  );

  const doPromote = useCallback(
    (ids: string[], kind: "scope" | "class", value: string) => {
      const path = kind === "scope" ? "promote-scope" : "promote-class";
      const body = kind === "scope" ? { target_scope: value } : { target_class: value };
      let remaining = ids.length;
      ids.forEach((id) => {
        customMutate(
          { url: `${apiUrl}/memory/${id}/${path}`, method: "post", values: body },
          {
            onSuccess: () => {
              remaining -= 1;
              if (remaining <= 0) {
                notifyOk(`Promoted ${ids.length} fact(s)`);
                setSelection([]);
                refetch();
              }
            },
            onError: () => notifyErr("Promote failed"),
          },
        );
      });
      setPromoteState(null);
    },
    [apiUrl, customMutate, refetch],
  );

  // --- Saved views -------------------------------------------------------
  const { data: savedViewsData, refetch: refetchViews } = useCustom<{
    items?: SavedView[];
    views?: SavedView[];
  }>({
    url: `${apiUrl}/dashboard/saved-views`,
    method: "get",
    config: { query: { resource: "memory" } },
  });
  const savedViews: SavedView[] = useMemo(() => {
    const d = savedViewsData?.data as
      | { items?: SavedView[]; views?: SavedView[] }
      | SavedView[]
      | undefined;
    if (!d) return [];
    if (Array.isArray(d)) return d;
    return d.items ?? d.views ?? [];
  }, [savedViewsData]);

  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const { mutate: saveViewMutate } = useCustomMutation();

  const saveCurrentView = useCallback(() => {
    if (!saveName.trim()) return;
    saveViewMutate(
      {
        url: `${apiUrl}/dashboard/saved-views`,
        method: "post",
        values: { name: saveName.trim(), resource: "memory", filters },
      },
      {
        onSuccess: () => {
          notifyOk("View saved");
          setSaveDialogOpen(false);
          setSaveName("");
          refetchViews();
        },
        onError: () => notifyErr("Save view failed"),
      },
    );
  }, [apiUrl, filters, saveName, saveViewMutate, refetchViews]);

  const loadView = useCallback(
    (viewId: string) => {
      const view = savedViews.find((v) => v.id === viewId);
      if (!view) return;
      const merged = { ...EMPTY_FILTERS, ...view.filters };
      setLocalFilters(merged);
      applyFilters(merged);
      if (view.sort) setSorters([{ field: view.sort.field, order: view.sort.order }]);
    },
    [savedViews, applyFilters, setSorters],
  );

  // DELETE /dashboard/saved-views/{view_id} — with confirmation dialog.
  const [deleteViewTarget, setDeleteViewTarget] = useState<SavedView | null>(null);
  const deleteView = useCallback(
    (view: SavedView) => {
      customMutate(
        {
          url: `${apiUrl}/dashboard/saved-views/${view.id}`,
          method: "delete",
          values: {},
        },
        {
          onSuccess: () => {
            notifyOk(`Deleted view "${view.name}"`);
            refetchViews();
          },
          onError: () => notifyErr("Delete view failed"),
        },
      );
      setDeleteViewTarget(null);
    },
    [apiUrl, customMutate, refetchViews],
  );

  // --- Columns -----------------------------------------------------------
  const columns = useMemo<GridColDef<FactAssertion>[]>(
    () => [
      {
        field: "text",
        headerName: "Text",
        flex: 2,
        minWidth: 260,
        sortable: false,
        renderCell: (params) => (
          <Tooltip title={params.value ?? ""}>
            <span>
              {String(params.value ?? "").slice(0, 100)}
              {String(params.value ?? "").length > 100 ? "…" : ""}
            </span>
          </Tooltip>
        ),
      },
      {
        field: "memory_class",
        headerName: "Type",
        width: 150,
        sortable: false,
        renderCell: (params) => <ClassChip value={params.value} />,
      },
      {
        field: "scope",
        headerName: "Scope",
        width: 130,
        sortable: false,
        renderCell: (params) => (
          <Chip size="small" variant="outlined" label={SCOPE_LABELS[params.value as Scope] ?? params.value} />
        ),
      },
      {
        field: "confidence",
        headerName: "Confidence",
        width: 150,
        renderCell: (params) => <ConfidenceBar value={params.value as number} />,
      },
      {
        field: "use_count",
        headerName: "Usage",
        width: 100,
        renderCell: (params) => <span>{params.value ?? 0} uses</span>,
      },
      {
        field: "successful_use_count",
        headerName: "Successful Uses",
        width: 140,
      },
      {
        field: "created_at",
        headerName: "Age",
        width: 150,
        renderCell: (params) => <span>{relativeAge(params.value as string)}</span>,
      },
      { field: "category", headerName: "Category", width: 130 },
      { field: "session_key", headerName: "Session Key", width: 180 },
      {
        field: "id",
        headerName: "ID",
        width: 220,
        sortable: false,
        renderCell: (params) => (
          <Tooltip title="Copy ID">
            <Button
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                navigator.clipboard?.writeText(String(params.value));
                notifyOk("ID copied");
              }}
              sx={{ textTransform: "none", fontFamily: "monospace" }}
            >
              {String(params.value).slice(0, 8)}…
            </Button>
          </Tooltip>
        ),
      },
      {
        field: "actions",
        type: "actions",
        headerName: "Actions",
        width: 130,
        getActions: (params) => [
          <GridActionsCellItem
            key="edit"
            icon={<EditIcon />}
            label="Edit"
            onClick={() => navigate(`/memory/${params.id}`)}
            showInMenu
          />,
          <GridActionsCellItem
            key="verify"
            icon={<VerifiedIcon />}
            label="Verify"
            disabled={!canEdit}
            onClick={() => verifyFact(String(params.id))}
            showInMenu
          />,
          <GridActionsCellItem
            key="forget"
            icon={<DeleteIcon />}
            label="Forget"
            disabled={!canDelete}
            onClick={() => setDeleteTarget([String(params.id)])}
            showInMenu
          />,
        ],
      },
    ],
    [canDelete, canEdit, navigate, verifyFact],
  );

  const columnVisibilityModel = {
    successful_use_count: false,
    category: false,
    session_key: false,
    id: false,
  };

  const CustomToolbar = () => (
    <GridToolbarContainer sx={{ gap: 1, flexWrap: "wrap", p: 1 }}>
      <GridToolbarColumnsButton />
      <GridToolbarDensitySelector />
      <GridToolbarExport
        csvOptions={{ fileName: "eb-memory-facts" }}
        printOptions={{ disableToolbarButton: true }}
      />
      <Button size="small" startIcon={<BookmarkIcon />} onClick={() => setSaveDialogOpen(true)}>
        Save View
      </Button>
      {savedViews.length > 0 && (
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel id="load-view-label">Load View</InputLabel>
          <Select
            labelId="load-view-label"
            label="Load View"
            value=""
            onChange={(e) => loadView(String(e.target.value))}
          >
            {savedViews.map((v) => (
              <MenuItem key={v.id} value={v.id}>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    width: "100%",
                    gap: 1,
                  }}
                >
                  <span>{v.name}</span>
                  <Tooltip title="Delete view">
                    <IconButton
                      size="small"
                      edge="end"
                      aria-label={`Delete saved view ${v.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteViewTarget(v);
                      }}
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      <Box sx={{ flex: 1 }} />
      {selectedIds.length > 0 && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">Selected: {selectedIds.length}</Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={!canEdit}
            onClick={() => setPromoteState({ ids: selectedIds, kind: "scope", value: "global" })}
          >
            Promote Scope
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={!canEdit}
            onClick={() =>
              setPromoteState({ ids: selectedIds, kind: "class", value: "semantic" })
            }
          >
            Promote Type
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="error"
            disabled={!canDelete}
            onClick={() => setDeleteTarget(selectedIds)}
          >
            Delete
          </Button>
        </Stack>
      )}
    </GridToolbarContainer>
  );

  return (
    <List title="Memory Browse">
      {/* Filter bar */}
      <Stack spacing={2} sx={{ mb: 2 }}>
        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap alignItems="center">
          <TextField
            label="Search text"
            size="small"
            value={filters.text}
            placeholder="Substring filter…"
            onChange={(e) => updateFilter({ text: e.target.value })}
            sx={{ minWidth: 240 }}
          />
          <FormControl size="small" sx={{ minWidth: 160 }}>
            <InputLabel id="scope-label">Scope</InputLabel>
            <Select
              labelId="scope-label"
              label="Scope"
              value={filters.scope}
              onChange={(e) => updateFilter({ scope: e.target.value as Scope | "" })}
            >
              <MenuItem value="">All</MenuItem>
              {SCOPE_OPTIONS.map((s) => (
                <MenuItem key={s} value={s}>
                  {SCOPE_LABELS[s]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 170 }}>
            <InputLabel id="class-label">Type</InputLabel>
            <Select
              labelId="class-label"
              label="Type"
              value={filters.memoryClass}
              onChange={(e) => updateFilter({ memoryClass: e.target.value as MemoryClass | "" })}
            >
              <MenuItem value="">All</MenuItem>
              {MEMORY_CLASS_OPTIONS.map((c) => (
                <MenuItem key={c} value={c}>
                  {MEMORY_CLASS_LABELS[c]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 170 }}>
            <InputLabel id="category-label">Category</InputLabel>
            <Select
              labelId="category-label"
              label="Category"
              value={filters.category}
              onChange={(e) => updateFilter({ category: String(e.target.value) })}
            >
              <MenuItem value="">All</MenuItem>
              {CATEGORY_OPTIONS.map((c) => (
                <MenuItem key={c} value={c}>
                  {CATEGORY_LABELS[c] ?? c}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Box sx={{ width: 220 }}>
            <Typography variant="caption">
              Minimum confidence: {filters.minConfidence.toFixed(2)}
            </Typography>
            <Slider
              size="small"
              value={filters.minConfidence}
              min={0}
              max={1}
              step={0.05}
              onChange={(_, v) => setLocalFilters((p) => ({ ...p, minConfidence: v as number }))}
              onChangeCommitted={(_, v) => updateFilter({ minConfidence: v as number })}
            />
          </Box>
        </Stack>
        {activeFilterChips.length > 0 && (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            {activeFilterChips.map((c) => (
              <Chip key={c.key} label={c.label} onDelete={c.clear} size="small" />
            ))}
            <Button size="small" onClick={clearFilters}>
              Clear all
            </Button>
          </Stack>
        )}
      </Stack>

      <Divider sx={{ mb: 1 }} />

      <DataGrid
        {...dataGridProps}
        columns={columns}
        getRowId={(row) => row.id}
        checkboxSelection
        disableRowSelectionOnClick
        onRowSelectionModelChange={(model) => setSelection(model)}
        rowSelectionModel={selection}
        onRowClick={(params) => navigate(`/memory/${params.id}`)}
        initialState={{ columns: { columnVisibilityModel } }}
        pageSizeOptions={[25, 50, 100, 200]}
        slots={{ toolbar: CustomToolbar }}
        autoHeight
        sx={{ "& .MuiDataGrid-row": { cursor: "pointer" } }}
      />

      {/* Delete confirmation */}
      <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>Delete {deleteTarget?.length ?? 0} fact(s)?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This permanently deletes {deleteTarget?.length ?? 0} fact(s) from all stores
            (Neo4j, Qdrant, Cognee). This cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button color="error" onClick={() => deleteTarget && doDelete(deleteTarget)}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Promote dialog */}
      <Dialog open={promoteState !== null} onClose={() => setPromoteState(null)}>
        <DialogTitle>
          Promote {promoteState?.kind === "scope" ? "Scope" : "Type"} ({promoteState?.ids.length}{" "}
          fact(s))
        </DialogTitle>
        <DialogContent sx={{ minWidth: 320 }}>
          <FormControl fullWidth size="small" sx={{ mt: 1 }}>
            <InputLabel id="promote-target-label">
              Target {promoteState?.kind === "scope" ? "scope" : "type"}
            </InputLabel>
            <Select
              labelId="promote-target-label"
              label={`Target ${promoteState?.kind === "scope" ? "scope" : "type"}`}
              value={promoteState?.value ?? ""}
              onChange={(e) =>
                setPromoteState((p) => (p ? { ...p, value: String(e.target.value) } : p))
              }
            >
              {(promoteState?.kind === "scope" ? SCOPE_OPTIONS : MEMORY_CLASS_OPTIONS).map((v) => (
                <MenuItem key={v} value={v}>
                  {promoteState?.kind === "scope"
                    ? SCOPE_LABELS[v as Scope]
                    : MEMORY_CLASS_LABELS[v as MemoryClass]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPromoteState(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() =>
              promoteState &&
              doPromote(promoteState.ids, promoteState.kind, promoteState.value)
            }
          >
            Promote
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete saved view confirmation */}
      <Dialog open={deleteViewTarget !== null} onClose={() => setDeleteViewTarget(null)}>
        <DialogTitle>Delete saved view?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This permanently deletes the saved view &quot;{deleteViewTarget?.name}&quot;. This
            cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteViewTarget(null)}>Cancel</Button>
          <Button
            color="error"
            onClick={() => deleteViewTarget && deleteView(deleteViewTarget)}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Save view dialog */}
      <Dialog open={saveDialogOpen} onClose={() => setSaveDialogOpen(false)}>
        <DialogTitle>Save current view</DialogTitle>
        <DialogContent sx={{ minWidth: 320 }}>
          <TextField
            autoFocus
            fullWidth
            label="View name"
            size="small"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={saveCurrentView} disabled={!saveName.trim()}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </List>
  );
};

export default MemoryList;
