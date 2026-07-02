// Memory Graph (`/memory/graph`) — Obsidian-style knowledge-graph explorer.
//
// Backend: `GET /dashboard/memory/graph` -> KnowledgeGraphResponse
// (gateway-scoped subgraph; Mode A = whole-gateway capped subgraph when no
// center_id, Mode B = BFS neighborhood when center_id is provided). Every
// request is scoped server-side to the operator's selected gateway — the
// dataProvider auto-injects it and the backend re-derives it from request.state,
// so this page NEVER supplies a client gateway_id.
//
// Rendering uses `react-force-graph-2d` (canvas d3-force; no three.js/WebGL). The
// component is code-split via React.lazy so its d3 bundle stays out of the main
// chunk. Encoding uses one visual channel per job: SHAPE encodes node type
// (Fact=circle, Actor=square, Goal=diamond, Artifact=triangle, Procedure=hexagon)
// and COLOR encodes ONLY the fact memory_class (non-fact nodes wear a single
// neutral fill). Nodes are sized by confidence/use_count, draggable, and
// pan/zoomable. Clicking a node routes to its detail page; right-clicking (or
// the search box) focuses its neighborhood.

import {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FC,
  type ReactElement,
} from "react";
import { useNavigate } from "react-router";
import { useApiUrl, useCustom } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControl,
  IconButton,
  InputLabel,
  Link as MuiLink,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import CloseIcon from "@mui/icons-material/Close";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import CenterFocusStrongIcon from "@mui/icons-material/CenterFocusStrong";

import type {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";

import { palette } from "../../theme";
import {
  MEMORY_CLASS_LABELS,
  SCOPE_LABELS,
  SCOPE_OPTIONS,
  type MemoryClass,
  type MemoryGraphNode,
  type MemoryGraphResponse,
  type Scope,
} from "./types";

// react-force-graph-2d pulls d3-force/zoom/drag; code-split it out of the main
// bundle. Its default export is the graph component. Typed as `any` here so the
// (heavily generic) library prop signatures don't fight the strict tsconfig —
// the component's contract is exercised through the typed helpers below.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ForceGraph2D = lazy(() => import("react-force-graph-2d")) as any;

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

type GNode = NodeObject<MemoryGraphNode>;
type GLink = LinkObject<MemoryGraphNode, { relation_type: string }>;

/** Content labels the backend graph endpoint is willing to return. */
const NODE_TYPES = [
  "FactDataPoint",
  "ActorDataPoint",
  "GoalDataPoint",
  "ArtifactDataPoint",
  "ProcedureDataPoint",
] as const;

const TYPE_LABELS: Record<string, string> = {
  FactDataPoint: "Fact",
  ActorDataPoint: "Actor",
  GoalDataPoint: "Goal",
  ArtifactDataPoint: "Artifact",
  ProcedureDataPoint: "Procedure",
};

// SHAPE encodes node type — one visual channel per job (color never encodes
// type). Geometry constants live in `traceShapePath` below.
type NodeShape = "circle" | "square" | "diamond" | "triangle" | "hexagon";

const NODE_SHAPES: Record<string, NodeShape> = {
  FactDataPoint: "circle",
  ActorDataPoint: "square",
  GoalDataPoint: "diamond",
  ArtifactDataPoint: "triangle",
  ProcedureDataPoint: "hexagon",
};

// COLOR encodes ONLY the fact memory_class. Validated categorical palette
// (validate_palette.js, light mode, surface #ffffff, --pairs all: lightness
// band, chroma floor, CVD separation, and >=3:1 contrast all PASS). Fixed
// order: episodic, semantic, procedural, policy, working_memory.
const CLASS_HEX: Record<MemoryClass, string> = {
  episodic: "#2a78d6",
  semantic: "#199e70",
  procedural: "#4a3aa7",
  policy: "#e34948",
  working_memory: "#c98500",
};

const MEMORY_CLASS_ORDER: MemoryClass[] = [
  "episodic",
  "semantic",
  "procedural",
  "policy",
  "working_memory",
];

// Neutral fill for non-fact nodes and unclassified facts (== palette
// textSecondary; OKLCH chroma 0.033 keeps it unmistakable for a class hue).
const NEUTRAL_HEX = "#5b6b7c";

const MAX_NODE_OPTIONS = [100, 300, 500, 1000, 2000];
const DEPTH_OPTIONS = [1, 2, 3];
const FAINT_LINK = "rgba(90, 107, 124, 0.22)";

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** Resolve the destination route for a clicked node (undefined => open drawer). */
function routeForNode(node: GNode): string | undefined {
  const t = node.type ?? "";
  const id = String(node.id);
  if (t.startsWith("Fact")) return `/memory/${id}`;
  if (t.startsWith("Actor")) return `/actors/${id}`;
  if (t.startsWith("Organization")) return `/organizations/${id}`;
  if (t.startsWith("Goal")) return `/goals`;
  if (t.startsWith("Procedure")) return `/procedures`;
  return undefined; // ArtifactDataPoint -> in-graph detail drawer
}

/** Shape by node type (unknown types fall back to circle). */
function shapeForNode(node: GNode): NodeShape {
  return NODE_SHAPES[node.type ?? ""] ?? "circle";
}

/**
 * Fill colour: facts wear their memory_class hue; everything else (and any
 * fact with a missing/unknown class) wears the neutral — type is shape-only.
 */
function colorForNode(node: GNode): string {
  const t = node.type ?? "";
  if (t.startsWith("Fact")) {
    const cls = String(node.properties?.memory_class ?? "") as MemoryClass;
    return CLASS_HEX[cls] ?? NEUTRAL_HEX;
  }
  return NEUTRAL_HEX;
}

/**
 * Trace the outline of a node glyph. Shared by nodeCanvasObject and
 * nodePointerAreaPaint so the hit area always matches the drawn mark. All
 * shapes are sized for approximately equal visual area to a circle of radius
 * `r` (equal-area multipliers on the circumradius).
 */
function traceShapePath(
  ctx: CanvasRenderingContext2D,
  shape: NodeShape,
  x: number,
  y: number,
  r: number,
): void {
  ctx.beginPath();
  switch (shape) {
    case "square": {
      const h = 0.89 * r; // half-side
      ctx.rect(x - h, y - h, 2 * h, 2 * h);
      break;
    }
    case "diamond": {
      const d = 1.25 * r; // vertex distance
      ctx.moveTo(x, y - d);
      ctx.lineTo(x + d, y);
      ctx.lineTo(x, y + d);
      ctx.lineTo(x - d, y);
      ctx.closePath();
      break;
    }
    case "triangle": {
      // Equilateral, apex up, circumradius 1.45r (bottom edge at y + 0.725r).
      const R = 1.45 * r;
      const w = R * (Math.sqrt(3) / 2);
      ctx.moveTo(x, y - R);
      ctx.lineTo(x + w, y + R / 2);
      ctx.lineTo(x - w, y + R / 2);
      ctx.closePath();
      break;
    }
    case "hexagon": {
      // Regular, pointy-top (vertex at 12 o'clock), circumradius 1.10r.
      const R = 1.1 * r;
      for (let k = 0; k < 6; k += 1) {
        const a = -Math.PI / 2 + (k * Math.PI) / 3;
        const px = x + R * Math.cos(a);
        const py = y + R * Math.sin(a);
        if (k === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
    }
    case "circle":
    default:
      ctx.arc(x, y, r, 0, 2 * Math.PI, false);
      break;
  }
}

/** Vertical extent below center (× r) per shape — keeps labels clear of marks. */
const SHAPE_EXTENT_Y: Record<NodeShape, number> = {
  circle: 1.0,
  square: 0.89,
  diamond: 1.25,
  triangle: 0.725, // apex-up: bottom edge sits at r * 0.725
  hexagon: 1.1,
};

/** Node radius (graph units) driven by confidence + use_count; floor 5 (>=10 diameter). */
function nodeRadius(node: GNode): number {
  const p = node.properties ?? {};
  const conf = typeof p.confidence === "number" ? p.confidence : 0.5;
  const uses = typeof p.use_count === "number" ? p.use_count : 0;
  return Math.max(5, 3 + conf * 4 + Math.min(Math.log2(1 + uses) * 1.5, 6));
}

/** eb_id of a link endpoint (force-graph swaps ids for node refs post-layout). */
function idOf(end: GLink["source"]): string {
  return end && typeof end === "object" ? String((end as GNode).id) : String(end);
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const MemoryGraph: FC = () => {
  const navigate = useNavigate();
  const apiUrl = useApiUrl();

  // --- Controls (query state) --------------------------------------------
  const [centerId, setCenterId] = useState<string | null>(null);
  const [depth, setDepth] = useState(1);
  const [maxNodes, setMaxNodes] = useState(300);
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(
    new Set(NODE_TYPES),
  );
  const [scopeFilter, setScopeFilter] = useState<string>(""); // client-side
  const [searchTerm, setSearchTerm] = useState("");

  // --- Interaction state --------------------------------------------------
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [drawerNode, setDrawerNode] = useState<GNode | null>(null);

  // node_types travels as a CSV; omit when the full set is selected (backend
  // defaults to the full allowed set, which keeps the query key stable).
  const typeCsv = useMemo(() => {
    const arr = [...selectedTypes];
    return arr.length > 0 && arr.length < NODE_TYPES.length
      ? arr.join(",")
      : undefined;
  }, [selectedTypes]);

  const query = useMemo(() => {
    const q: Record<string, unknown> = { max_nodes: maxNodes };
    if (centerId) {
      q.center_id = centerId;
      q.depth = depth;
    }
    if (typeCsv) q.node_types = typeCsv;
    return q;
  }, [centerId, depth, maxNodes, typeCsv]);

  const { data, isLoading, isError } = useCustom<MemoryGraphResponse>({
    url: `${apiUrl}/dashboard/memory/graph`,
    method: "get",
    config: { query },
  });

  const resp = data?.data;

  // --- Build force-graph data (rename edges -> links) + apply scope filter -
  const graphData = useMemo(() => {
    const rawNodes = resp?.nodes ?? [];
    const rawEdges = resp?.edges ?? [];
    const keptNodes = scopeFilter
      ? rawNodes.filter((n) => String(n.properties?.scope ?? "") === scopeFilter)
      : rawNodes;
    const keep = new Set(keptNodes.map((n) => n.id));
    const nodes: GNode[] = keptNodes.map((n) => ({
      id: n.id,
      type: n.type,
      label: n.label,
      properties: n.properties ?? {},
    }));
    const links: GLink[] = rawEdges
      .filter((e) => keep.has(e.source) && keep.has(e.target))
      .map((e) => ({
        source: e.source,
        target: e.target,
        relation_type: e.relation_type,
      }));
    return { nodes, links };
  }, [resp, scopeFilter]);

  // Adjacency for hover highlighting.
  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of graphData.links) {
      const s = idOf(l.source);
      const t = idOf(l.target);
      if (!m.has(s)) m.set(s, new Set());
      if (!m.has(t)) m.set(t, new Set());
      m.get(s)!.add(t);
      m.get(t)!.add(s);
    }
    return m;
  }, [graphData]);

  // Nodes matching the live search term (null => no active search).
  const matched = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    if (!q) return null;
    const s = new Set<string>();
    for (const n of graphData.nodes) {
      if (
        (n.label ?? "").toLowerCase().includes(q) ||
        String(n.id).toLowerCase().includes(q)
      ) {
        s.add(String(n.id));
      }
    }
    return s;
  }, [searchTerm, graphData]);

  // --- Canvas sizing ------------------------------------------------------
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [dims, setDims] = useState({ width: 800, height: 560 });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) {
        setDims({
          width: Math.max(320, Math.floor(cr.width)),
          height: Math.max(320, Math.floor(cr.height)),
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // --- Force-graph ref + zoom-to-fit on (re)load --------------------------
  const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
  const didFitRef = useRef(false);
  useEffect(() => {
    didFitRef.current = false;
  }, [graphData]);
  const handleEngineStop = () => {
    if (!didFitRef.current) {
      fgRef.current?.zoomToFit(400, 60);
      didFitRef.current = true;
    }
  };

  // --- Node/link paint ----------------------------------------------------
  const drawNode = (node: GNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const id = String(node.id);
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const r = nodeRadius(node);

    const isHover = id === hoverId;
    const isCenter = id === centerId;
    const isMatch = matched?.has(id) ?? false;
    const neighbors = hoverId ? adjacency.get(hoverId) : undefined;

    let alpha = 1;
    if (matched && !isMatch) alpha = 0.12;
    if (hoverId && !isHover && !(neighbors && neighbors.has(id))) {
      alpha = Math.min(alpha, 0.15);
    }

    const shape = shapeForNode(node);

    ctx.save();
    ctx.globalAlpha = alpha;
    traceShapePath(ctx, shape, x, y, r);
    ctx.fillStyle = colorForNode(node);
    ctx.fill();

    // Constant 2 screen-px surface-coloured ring on EVERY node so overlapping
    // marks separate (never a dark border around a mark).
    ctx.lineWidth = 2 / scale;
    ctx.strokeStyle = palette.paper;
    ctx.stroke();

    // Emphasis: an additional outer ring in navy — the single emphasis ink
    // (never palette.error, which would camouflage against the policy fill).
    // Width differentiates the center node.
    if (isHover || isCenter || isMatch) {
      traceShapePath(ctx, shape, x, y, r + 3 / scale);
      ctx.lineWidth = (isCenter ? 3 : 2) / scale;
      ctx.strokeStyle = palette.navy;
      ctx.stroke();
    }

    const showLabel = scale > 1.3 || isHover || isCenter || isMatch;
    if (showLabel) {
      const fontSize = Math.max(10 / scale, 2);
      ctx.font = `${fontSize}px Inter, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      // Labels always wear text ink — never a class hue or the neutral fill.
      ctx.fillStyle = palette.textPrimary;
      ctx.globalAlpha = Math.max(alpha, 0.75);
      ctx.fillText(
        truncate(node.label || id, 24),
        x,
        y + r * SHAPE_EXTENT_Y[shape] + 3.5 / scale,
      );
    }
    ctx.restore();
  };

  const paintPointerArea = (
    node: GNode,
    color: string,
    ctx: CanvasRenderingContext2D,
    scale: number,
  ) => {
    // Same shape footprint as the drawn glyph (+ the surface ring) so
    // hover/click hit-targets match what the user sees.
    ctx.fillStyle = color;
    traceShapePath(
      ctx,
      shapeForNode(node),
      node.x ?? 0,
      node.y ?? 0,
      nodeRadius(node) + 2 / scale,
    );
    ctx.fill();
  };

  const linkColor = (link: GLink): string => {
    const s = idOf(link.source);
    const t = idOf(link.target);
    if (hoverId && (s === hoverId || t === hoverId)) return palette.teal;
    if (matched && matched.has(s) && matched.has(t)) return palette.tealDark;
    return FAINT_LINK;
  };

  const linkWidth = (link: GLink): number => {
    const s = idOf(link.source);
    const t = idOf(link.target);
    return hoverId && (s === hoverId || t === hoverId) ? 2 : 0.6;
  };

  // --- Interaction handlers ----------------------------------------------
  const handleNodeClick = (node: GNode) => {
    const route = routeForNode(node);
    if (route) {
      navigate(route);
    } else {
      setDrawerNode(node);
    }
  };

  const handleNodeRightClick = (node: GNode, event: MouseEvent) => {
    event.preventDefault?.();
    setCenterId(String(node.id));
    setDepth(1);
  };

  const handleNodeDragEnd = (node: GNode) => {
    // Pin the node where it was dropped (Obsidian-like stability).
    node.fx = node.x;
    node.fy = node.y;
  };

  const focusSearch = () => {
    const q = searchTerm.trim();
    if (!q) return;
    const first = graphData.nodes.find(
      (n) =>
        (n.label ?? "").toLowerCase().includes(q.toLowerCase()) ||
        String(n.id).toLowerCase() === q.toLowerCase(),
    );
    // Focus the neighborhood (Mode B). If nothing matches locally, treat the
    // term as a raw eb_id so operators can jump straight to a known node.
    setCenterId(first ? String(first.id) : q);
  };

  const toggleType = (t: string) => {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      // Never allow an empty selection (would silently mean "all" server-side).
      return next.size > 0 ? next : new Set(NODE_TYPES);
    });
  };

  const resetView = () => {
    setCenterId(null);
    setDepth(1);
    setScopeFilter("");
    setSearchTerm("");
    setSelectedTypes(new Set(NODE_TYPES));
  };

  const centerLabel = useMemo(() => {
    if (!centerId) return "";
    const n = graphData.nodes.find((x) => String(x.id) === centerId);
    return n?.label || centerId;
  }, [centerId, graphData]);

  const nodeCount = resp?.node_count ?? graphData.nodes.length;
  const edgeCount = resp?.edge_count ?? graphData.links.length;
  const truncated = resp?.truncated ?? false;
  const isEmpty = !isLoading && !isError && graphData.nodes.length === 0;

  // --- Legend entries (only types actually present) -----------------------
  const presentTypes = useMemo(() => {
    const set = new Set<string>();
    for (const n of graphData.nodes) set.add(n.type);
    return NODE_TYPES.filter((t) => set.has(t));
  }, [graphData]);

  const hasFacts = graphData.nodes.some((n) => n.type.startsWith("Fact"));

  // Any present fact with a missing/unknown memory_class => show the
  // "Unclassified" legend row.
  const hasUnclassifiedFacts = useMemo(
    () =>
      graphData.nodes.some(
        (n) =>
          n.type.startsWith("Fact") &&
          !(String(n.properties?.memory_class ?? "") in CLASS_HEX),
      ),
    [graphData],
  );

  return (
    <Box
      sx={{
        p: 2,
        height: "calc(100vh - 96px)",
        minHeight: 520,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Header */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Box>
          <Typography variant="h5">Memory Graph</Typography>
          <Typography variant="caption" color="text.secondary">
            {centerId
              ? `Focused neighborhood · depth ${depth}`
              : "Whole-gateway subgraph"}{" "}
            · {nodeCount.toLocaleString()} nodes · {edgeCount.toLocaleString()}{" "}
            edges
          </Typography>
        </Box>
        <Button
          size="small"
          startIcon={<RestartAltIcon />}
          onClick={resetView}
        >
          Reset view
        </Button>
      </Stack>

      {/* Controls */}
      <Card sx={{ mb: 1.5 }}>
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack spacing={1.5}>
            {/* Search / focus */}
            <Stack direction="row" spacing={1} alignItems="center">
              <TextField
                size="small"
                fullWidth
                placeholder="Search a node label, or paste an eb_id to focus…"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") focusSearch();
                }}
                InputProps={{
                  startAdornment: (
                    <SearchIcon fontSize="small" sx={{ mr: 1, opacity: 0.6 }} />
                  ),
                }}
              />
              <Tooltip title="Focus this node's neighborhood (Mode B)">
                <span>
                  <Button
                    variant="outlined"
                    startIcon={<CenterFocusStrongIcon />}
                    onClick={focusSearch}
                    disabled={!searchTerm.trim()}
                  >
                    Focus
                  </Button>
                </span>
              </Tooltip>
            </Stack>

            {/* Filters */}
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              flexWrap="wrap"
              useFlexGap
            >
              {/* Type chips are neutral — type is encoded by SHAPE only, so no
                  per-type colour may appear here. Selected = navy fill (an
                  interaction state, not a data encoding). */}
              {NODE_TYPES.map((t) => {
                const on = selectedTypes.has(t);
                return (
                  <Chip
                    key={t}
                    size="small"
                    icon={
                      <ShapeGlyph
                        shape={NODE_SHAPES[t]}
                        size={10}
                        fill={on ? "#fff" : NEUTRAL_HEX}
                      />
                    }
                    label={TYPE_LABELS[t]}
                    variant={on ? "filled" : "outlined"}
                    onClick={() => toggleType(t)}
                    sx={
                      on
                        ? {
                            bgcolor: palette.navy,
                            color: "#fff",
                            "&:hover": { bgcolor: palette.navyDeep },
                          }
                        : {
                            borderColor: palette.border,
                            color: "text.secondary",
                          }
                    }
                  />
                );
              })}

              <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />

              <FormControl size="small" sx={{ minWidth: 150 }}>
                <InputLabel id="scope-label">Scope</InputLabel>
                <Select
                  labelId="scope-label"
                  label="Scope"
                  value={scopeFilter}
                  onChange={(e) => setScopeFilter(String(e.target.value))}
                >
                  <MenuItem value="">All scopes</MenuItem>
                  {SCOPE_OPTIONS.map((s) => (
                    <MenuItem key={s} value={s}>
                      {SCOPE_LABELS[s as Scope] ?? s}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              <FormControl size="small" sx={{ minWidth: 130 }}>
                <InputLabel id="maxnodes-label">Max nodes</InputLabel>
                <Select
                  labelId="maxnodes-label"
                  label="Max nodes"
                  value={maxNodes}
                  onChange={(e) => setMaxNodes(Number(e.target.value))}
                >
                  {MAX_NODE_OPTIONS.map((n) => (
                    <MenuItem key={n} value={n}>
                      {n}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {centerId && (
                <FormControl size="small" sx={{ minWidth: 110 }}>
                  <InputLabel id="depth-label">Depth</InputLabel>
                  <Select
                    labelId="depth-label"
                    label="Depth"
                    value={depth}
                    onChange={(e) => setDepth(Number(e.target.value))}
                  >
                    {DEPTH_OPTIONS.map((d) => (
                      <MenuItem key={d} value={d}>
                        {d} hop{d > 1 ? "s" : ""}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              {centerId && (
                <Chip
                  color="primary"
                  label={`Focused: ${truncate(centerLabel, 28)}`}
                  onDelete={() => setCenterId(null)}
                />
              )}
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {/* Truncation warning */}
      {truncated && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          Showing the first {maxNodes.toLocaleString()} nodes — the subgraph is
          larger. Narrow the type filter, lower the scope, or focus a node to see
          more detail.
        </Alert>
      )}

      {/* Graph surface */}
      {isError ? (
        <Alert severity="error">
          Could not load the memory graph. The graph store may be unavailable.
        </Alert>
      ) : (
        <Box
          ref={wrapRef}
          sx={{
            flex: 1,
            minHeight: 0,
            position: "relative",
            border: `1px solid ${palette.border}`,
            borderRadius: 2,
            overflow: "hidden",
            bgcolor: palette.paper,
            cursor: hoverId ? "pointer" : "grab",
          }}
        >
          <Suspense
            fallback={
              <Box
                sx={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <CircularProgress />
              </Box>
            }
          >
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              width={dims.width}
              height={dims.height}
              backgroundColor={palette.paper}
              nodeRelSize={4}
              nodeVal={(n: GNode) => Math.max(1, nodeRadius(n))}
              nodeLabel={(n: GNode) => {
                const base = `${n.label || String(n.id)} · ${
                  TYPE_LABELS[n.type] ?? n.type
                }`;
                if (!(n.type ?? "").startsWith("Fact")) return base;
                const cls = String(
                  n.properties?.memory_class ?? "",
                ) as MemoryClass;
                return `${base} · ${
                  cls in CLASS_HEX ? MEMORY_CLASS_LABELS[cls] : "Unclassified"
                }`;
              }}
              nodeCanvasObject={drawNode}
              nodePointerAreaPaint={paintPointerArea}
              linkColor={linkColor}
              linkWidth={linkWidth}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              linkDirectionalArrowColor={linkColor}
              onNodeClick={handleNodeClick}
              onNodeHover={(n: GNode | null) =>
                setHoverId(n ? String(n.id) : null)
              }
              onNodeRightClick={handleNodeRightClick}
              onNodeDragEnd={handleNodeDragEnd}
              onEngineStop={handleEngineStop}
              cooldownTicks={120}
              d3VelocityDecay={0.3}
            />
          </Suspense>

          {/* Loading overlay */}
          {isLoading && (
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: "rgba(255,255,255,0.6)",
              }}
            >
              <CircularProgress />
            </Box>
          )}

          {/* Empty state */}
          {isEmpty && (
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                textAlign: "center",
                p: 3,
              }}
            >
              <Typography variant="subtitle1" gutterBottom>
                No graph to show
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {centerId
                  ? "This node has no connections in the current gateway, or it was not found."
                  : "No memory nodes match the current filters for this gateway."}
              </Typography>
              {centerId && (
                <Button sx={{ mt: 2 }} onClick={() => setCenterId(null)}>
                  Back to whole-gateway view
                </Button>
              )}
            </Box>
          )}

          {/* Legend — ONE box, two orthogonal channels: shape carries type
              (every glyph neutral, never a class hue), colour carries memory
              class (every chip the same circle, label text never coloured). */}
          {presentTypes.length > 0 && (
            <Card
              variant="outlined"
              sx={{
                position: "absolute",
                top: 12,
                right: 12,
                maxWidth: 230,
                bgcolor: "rgba(255,255,255,0.92)",
              }}
            >
              <CardContent sx={{ p: 1.25, "&:last-child": { pb: 1.25 } }}>
                <Typography variant="caption" color="text.secondary">
                  Shape — node type
                </Typography>
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {presentTypes.map((t) => (
                    <Stack
                      key={t}
                      direction="row"
                      spacing={1}
                      alignItems="center"
                    >
                      <ShapeGlyph
                        shape={NODE_SHAPES[t]}
                        size={12}
                        fill={NEUTRAL_HEX}
                      />
                      <Typography variant="caption">
                        {TYPE_LABELS[t]}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
                {hasFacts && (
                  <>
                    <Divider sx={{ my: 0.75 }} />
                    <Typography variant="caption" color="text.secondary">
                      Color — memory class
                    </Typography>
                    <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                      {MEMORY_CLASS_ORDER.map((cls) => (
                        <LegendRow
                          key={cls}
                          color={CLASS_HEX[cls]}
                          label={MEMORY_CLASS_LABELS[cls] ?? cls}
                        />
                      ))}
                      {hasUnclassifiedFacts && (
                        <LegendRow color={NEUTRAL_HEX} label="Unclassified" />
                      )}
                    </Stack>
                  </>
                )}
              </CardContent>
            </Card>
          )}
        </Box>
      )}

      {/* Artifact / detail drawer */}
      <Drawer
        anchor="right"
        open={Boolean(drawerNode)}
        onClose={() => setDrawerNode(null)}
      >
        <Box sx={{ width: 340, p: 2 }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle1">
              {drawerNode ? TYPE_LABELS[drawerNode.type] ?? drawerNode.type : ""}
            </Typography>
            <IconButton size="small" onClick={() => setDrawerNode(null)}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>
          {drawerNode && (
            <>
              <Typography variant="body1" gutterBottom>
                {drawerNode.label || String(drawerNode.id)}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
              >
                {String(drawerNode.id)}
              </Typography>
              <Divider sx={{ my: 1.5 }} />
              <Table size="small">
                <TableBody>
                  {Object.entries(drawerNode.properties ?? {})
                    .filter(([, v]) => v !== null && v !== undefined && v !== "")
                    .map(([k, v]) => (
                      <TableRow key={k}>
                        <TableCell sx={{ color: "text.secondary", border: 0, py: 0.5 }}>
                          {k}
                        </TableCell>
                        <TableCell sx={{ border: 0, py: 0.5, wordBreak: "break-word" }}>
                          {String(v)}
                        </TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
              <Divider sx={{ my: 1.5 }} />
              <Stack spacing={1}>
                <Button
                  variant="outlined"
                  startIcon={<CenterFocusStrongIcon />}
                  onClick={() => {
                    setCenterId(String(drawerNode.id));
                    setDepth(1);
                    setDrawerNode(null);
                  }}
                >
                  Focus neighborhood
                </Button>
                <MuiLink
                  component="button"
                  variant="caption"
                  onClick={() => setDrawerNode(null)}
                >
                  Close
                </MuiLink>
              </Stack>
            </>
          )}
        </Box>
      </Drawer>
    </Box>
  );
};

// Inline-SVG glyph of a node shape, using the SAME geometry constants as the
// canvas draw code (equal-area multipliers on the circumradius). The base
// radius is sized so the largest extent (triangle, 1.45r) fills the box.
const ShapeGlyph: FC<{ shape: NodeShape; size: number; fill: string }> = ({
  shape,
  size,
  fill,
}) => {
  const c = size / 2;
  const r = c / 1.45;
  const rnd = (v: number) => Math.round(v * 100) / 100;
  const poly = (pts: Array<[number, number]>) => (
    <polygon points={pts.map(([px, py]) => `${rnd(px)},${rnd(py)}`).join(" ")} fill={fill} />
  );

  let glyph: ReactElement;
  switch (shape) {
    case "square": {
      const h = 0.89 * r;
      glyph = (
        <rect x={rnd(c - h)} y={rnd(c - h)} width={rnd(2 * h)} height={rnd(2 * h)} fill={fill} />
      );
      break;
    }
    case "diamond": {
      const d = 1.25 * r;
      glyph = poly([
        [c, c - d],
        [c + d, c],
        [c, c + d],
        [c - d, c],
      ]);
      break;
    }
    case "triangle": {
      const R = 1.45 * r;
      const w = R * (Math.sqrt(3) / 2);
      glyph = poly([
        [c, c - R],
        [c + w, c + R / 2],
        [c - w, c + R / 2],
      ]);
      break;
    }
    case "hexagon": {
      const R = 1.1 * r;
      glyph = poly(
        Array.from({ length: 6 }, (_, k) => {
          const a = -Math.PI / 2 + (k * Math.PI) / 3;
          return [c + R * Math.cos(a), c + R * Math.sin(a)] as [number, number];
        }),
      );
      break;
    }
    case "circle":
    default:
      glyph = <circle cx={c} cy={c} r={rnd(r)} fill={fill} />;
      break;
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      style={{ flexShrink: 0, display: "block" }}
      aria-hidden
    >
      {glyph}
    </svg>
  );
};

// Colour-chip legend row: deliberately always a CIRCLE, because class colour
// only ever appears on fact circles in the graph — shape never varies here.
const LegendRow: FC<{ color: string; label: string }> = ({ color, label }) => (
  <Stack direction="row" spacing={1} alignItems="center">
    <Box
      sx={{
        width: 10,
        height: 10,
        borderRadius: "50%",
        bgcolor: color,
        flexShrink: 0,
      }}
    />
    <Typography variant="caption">{label}</Typography>
  </Stack>
);

export default MemoryGraph;
