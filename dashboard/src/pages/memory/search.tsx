// Memory Search (`/memory/search`) — semantic search interface.
//
// Distinct from the browse DataGrid. Uses the existing Phase 4 endpoint
// `POST /memory/search` directly (no new backend). Profile-driven retrieval
// with an optional auto-recall simulation toggle.
//
// Implements plan Section 2 "Memory Search" + SOW page 3.

import { useState, type FC } from "react";
import { useNavigate } from "react-router";
import { useSearchParams } from "react-router-dom";
import { useApiUrl, useCustomMutation } from "@refinedev/core";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import SearchIcon from "@mui/icons-material/Search";

import { errorMessage } from "../../lib/errors";
import { humanizeEnum, pluralize } from "../../lib/format";
import {
  MEMORY_CLASS_COLORS,
  MEMORY_CLASS_HEX,
  MEMORY_CLASS_LABELS,
  PROFILE_OPTIONS,
  SCOPE_LABELS,
  SOURCE_TOOLTIPS,
  sourceLabel,
  type MemoryClass,
  type Scope,
  type SearchResult,
} from "./types";

const MAX_RESULT_OPTIONS = [10, 20, 50];

// memory-search-5: preserve the last query + results across in-app navigation
// (e.g. click a result → Back). The component unmounts on navigate, so a
// module-level cache keyed by the exact inputs restores the view without
// re-issuing the request. Inputs also live in the URL so a reload/deep-link
// rehydrates the search box (results are re-run by the operator on reload).
interface SearchInputs {
  query: string;
  profile: string;
  maxResults: number;
  autoRecall: boolean;
}
interface ResultsSnapshot {
  key: string;
  results: SearchResult[] | null;
  elapsed: number | null;
  error: string | null;
}
let resultsCache: ResultsSnapshot | null = null;

const inputsKey = (i: SearchInputs): string =>
  JSON.stringify([i.query.trim(), i.profile, i.maxResults, i.autoRecall]);

export const MemorySearch: FC = () => {
  const navigate = useNavigate();
  const apiUrl = useApiUrl();
  const { mutate, isLoading } = useCustomMutation<SearchResult[]>();
  const [searchParams, setSearchParams] = useSearchParams();

  // Inputs are seeded from the URL (deep-link / reload survival).
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [profile, setProfile] = useState(
    () => searchParams.get("profile") ?? "coding",
  );
  const [maxResults, setMaxResults] = useState(() => {
    const n = Number(searchParams.get("max"));
    return MAX_RESULT_OPTIONS.includes(n) ? n : 20;
  });
  const [autoRecall, setAutoRecall] = useState(
    () => searchParams.get("recall") === "1",
  );

  // Results / error state, hydrated from the module cache when the initial
  // (URL-seeded) inputs match the last executed search — back-nav restores the
  // exact prior view without re-issuing the request (memory-search-5).
  const initialKey = inputsKey({ query, profile, maxResults, autoRecall });
  const [results, setResults] = useState<SearchResult[] | null>(() =>
    resultsCache && resultsCache.key === initialKey ? resultsCache.results : null,
  );
  const [elapsed, setElapsed] = useState<number | null>(() =>
    resultsCache && resultsCache.key === initialKey ? resultsCache.elapsed : null,
  );
  const [error, setError] = useState<string | null>(() =>
    resultsCache && resultsCache.key === initialKey ? resultsCache.error : null,
  );

  const runSearch = () => {
    // memory-search-3: honour the in-flight guard so Enter (or a double click)
    // cannot race a second request whose stale response overwrites the first.
    if (!query.trim() || isLoading) return;
    setError(null);

    // Snapshot the EXECUTED inputs — the module cache is keyed by these (not the
    // live, possibly-still-being-edited inputs) so a later restore is coherent.
    const executed: SearchInputs = { query: query.trim(), profile, maxResults, autoRecall };
    const key = inputsKey(executed);

    // Reflect the executed inputs in the URL (shareable / reload-safe).
    const next = new URLSearchParams();
    next.set("q", executed.query);
    next.set("profile", profile);
    next.set("max", String(maxResults));
    if (autoRecall) next.set("recall", "1");
    setSearchParams(next, { replace: true });

    const started = performance.now();
    mutate(
      {
        url: `${apiUrl}/memory/search`,
        method: "post",
        values: {
          query: executed.query,
          max_results: maxResults,
          profile_name: profile,
          auto_recall: autoRecall,
        },
      },
      {
        onSuccess: (resp) => {
          const el = (performance.now() - started) / 1000;
          const raw = resp?.data as unknown;
          const list: SearchResult[] = Array.isArray(raw)
            ? (raw as SearchResult[])
            : ((raw as { results?: SearchResult[] })?.results ?? []);
          setElapsed(el);
          setResults(list);
          setError(null);
          resultsCache = { key, results: list, elapsed: el, error: null };
        },
        onError: (err) => {
          // memory-search-4: one coherent error state — surface the real backend
          // message and DON'T also render an empty "0 results" block.
          const msg = errorMessage(err);
          setError(msg);
          setResults(null);
          setElapsed(null);
          resultsCache = { key, results: null, elapsed: null, error: msg };
        },
      },
    );
  };

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Memory Search
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              fullWidth
              placeholder="Ask a question about what the system remembers..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") runSearch();
              }}
            />
            <Button
              variant="contained"
              startIcon={<SearchIcon />}
              onClick={runSearch}
              disabled={isLoading || !query.trim()}
            >
              Search
            </Button>
          </Stack>

          <Stack
            direction="row"
            spacing={2}
            sx={{ mt: 2 }}
            flexWrap="wrap"
            useFlexGap
            alignItems="center"
          >
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel id="profile-label">Profile</InputLabel>
              <Select
                labelId="profile-label"
                label="Profile"
                value={profile}
                onChange={(e) => setProfile(String(e.target.value))}
              >
                {PROFILE_OPTIONS.map((p) => (
                  <MenuItem key={p} value={p}>
                    {humanizeEnum(p)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel id="max-label">Max results</InputLabel>
              <Select
                labelId="max-label"
                label="Max results"
                value={maxResults}
                onChange={(e) => setMaxResults(Number(e.target.value))}
              >
                {MAX_RESULT_OPTIONS.map((n) => (
                  <MenuItem key={n} value={n}>
                    {n}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControlLabel
              control={
                <Switch
                  checked={autoRecall}
                  onChange={(e) => setAutoRecall(e.target.checked)}
                />
              }
              label="Simulate auto-recall"
            />
          </Stack>
        </CardContent>
      </Card>

      {isLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
          <CircularProgress />
        </Box>
      )}

      {!isLoading && error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {results !== null && !isLoading && !error && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {results.length} {pluralize(results.length, "result")}
            {elapsed !== null ? ` (${elapsed.toFixed(2)}s)` : ""}
          </Typography>
          {results.length === 0 ? (
            <Typography color="text.secondary">
              No results. Try a different query or profile.
            </Typography>
          ) : (
            <Stack spacing={1.5}>
              {results.map((r) => {
                const cls = r.memory_class as MemoryClass;
                const clsHex = MEMORY_CLASS_HEX[cls];
                const pct = Math.round((r.score ?? 0) * 100);
                const srcLabel = sourceLabel(r.source);
                return (
                  <Card key={r.id} variant="outlined">
                    <CardActionArea onClick={() => navigate(`/memory/${r.id}`)}>
                      <CardContent>
                        <Typography variant="body1">{r.text}</Typography>
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          sx={{ mt: 1 }}
                          flexWrap="wrap"
                          useFlexGap
                        >
                          <Chip
                            size="small"
                            label={MEMORY_CLASS_LABELS[cls] ?? r.memory_class}
                            color={clsHex ? "default" : MEMORY_CLASS_COLORS[cls] ?? "default"}
                            sx={clsHex ? { bgcolor: clsHex, color: "#fff" } : undefined}
                          />
                          <Tooltip title={SOURCE_TOOLTIPS[srcLabel] ?? srcLabel}>
                            <Chip size="small" variant="outlined" label={srcLabel} />
                          </Tooltip>
                          <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 160 }}>
                            <LinearProgress
                              variant="determinate"
                              value={pct}
                              sx={{ width: 100, height: 8, borderRadius: 1 }}
                            />
                            <Typography variant="caption">{pct}%</Typography>
                          </Box>
                        </Stack>
                      </CardContent>
                    </CardActionArea>
                    <Accordion disableGutters elevation={0}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                        <Typography variant="caption">Details</Typography>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Stack spacing={0.5}>
                          <Typography variant="caption">Raw score: {r.score?.toFixed(3)}</Typography>
                          <Typography variant="caption">
                            Scope: {SCOPE_LABELS[r.scope as Scope] ?? r.scope}
                          </Typography>
                          <Typography variant="caption">
                            Confidence: {(r.confidence ?? 0).toFixed(2)} · Uses: {r.use_count ?? 0}
                          </Typography>
                          {r.session_key && (
                            <Typography variant="caption">Session: {r.session_key}</Typography>
                          )}
                          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                            ID: {r.id}
                          </Typography>
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  </Card>
                );
              })}
            </Stack>
          )}
        </>
      )}
    </Box>
  );
};

export default MemorySearch;
