// Effective Config viewer (authority >= 90 — backend GET /config/effective is a
// WRITE route).
//
// Read-only view of the resolved runtime config with secret-looking values
// masked server-side ("***MASKED***"). The endpoint returns `{ config: {...} }`
// as an arbitrarily-nested dict; we render each top-level section as its own
// collapsible JSON tree so nested objects stay legible and the page keeps to a
// sane width (settings-5) instead of dumping one giant unwrapped JSON string.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Paper,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { apiGet, useAuthority } from "../home/dashboardApi";
import { errorMessage } from "../../lib/errors";
import { humanizeEnum } from "../../lib/format";

const MASK_SENTINEL = "***MASKED***";

/** Render a scalar config value (masked values become a subtle chip). */
function ScalarValue({ value }: { value: unknown }) {
  if (value === MASK_SENTINEL) {
    return <Chip size="small" variant="outlined" label="masked" color="warning" />;
  }
  const text =
    value === null
      ? "null"
      : value === undefined
        ? "—"
        : typeof value === "string"
          ? value
          : String(value);
  return (
    <Typography
      component="span"
      variant="body2"
      sx={{
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        color: "text.primary",
        wordBreak: "break-word",
        whiteSpace: "pre-wrap",
      }}
    >
      {text}
    </Typography>
  );
}

/**
 * Recursive collapsible node. Objects/arrays render an expandable header with a
 * child count; scalars render `key: value` on one wrapping row. Depth drives
 * indentation and the default-open behaviour (top levels open, deep levels
 * collapsed) so the tree never overflows the page.
 */
function ConfigNode({
  label,
  value,
  depth,
}: {
  label: string;
  value: unknown;
  depth: number;
}) {
  const isContainer = value !== null && typeof value === "object";
  const [open, setOpen] = useState(depth < 2);

  if (!isContainer) {
    return (
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "baseline",
          py: 0.25,
          pl: `${depth * 16}px`,
        }}
      >
        <Typography
          variant="body2"
          sx={{ color: "text.secondary", flexShrink: 0, wordBreak: "break-word" }}
        >
          {label}
        </Typography>
        <ScalarValue value={value} />
      </Box>
    );
  }

  const entries: Array<[string, unknown]> = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as [string, unknown])
    : Object.entries(value as Record<string, unknown>);
  const summary = Array.isArray(value)
    ? `[${entries.length}]`
    : `{${entries.length}}`;

  return (
    <Box sx={{ pl: `${depth * 16}px` }}>
      <Box
        role="button"
        tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
          py: 0.25,
          userSelect: "none",
        }}
      >
        <ExpandMoreIcon
          fontSize="small"
          sx={{
            transform: open ? "none" : "rotate(-90deg)",
            transition: "transform 0.15s",
            color: "text.disabled",
          }}
        />
        <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: "break-word" }}>
          {label}
        </Typography>
        <Typography variant="caption" sx={{ ml: 1, color: "text.disabled" }}>
          {summary}
        </Typography>
      </Box>
      <Collapse in={open} unmountOnExit>
        {entries.length === 0 ? (
          <Typography
            variant="caption"
            sx={{ pl: `${(depth + 1) * 16}px`, color: "text.disabled" }}
          >
            (empty)
          </Typography>
        ) : (
          entries.map(([k, v]) => (
            <ConfigNode key={k} label={k} value={v} depth={depth + 1} />
          ))
        )}
      </Collapse>
    </Box>
  );
}

export const EffectiveConfigPage: React.FC = () => {
  const authority = useAuthority();
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<Record<string, unknown>>("/dashboard/config/effective"));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (authority < 90) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="warning">
          Effective config requires authority &ge; 90.
        </Alert>
      </Box>
    );
  }

  // The endpoint wraps the resolved config under `config`; unwrap it so each
  // real section becomes its own tree (rather than one "config" super-section).
  const config =
    data && typeof data.config === "object" && data.config !== null
      ? (data.config as Record<string, unknown>)
      : (data ?? {});
  const sections = Object.entries(config);

  return (
    <Box sx={{ p: 2, maxWidth: 900 }}>
      <Typography variant="h5" gutterBottom>
        Effective Config
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress />
      ) : sections.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No configuration available.
        </Typography>
      ) : (
        sections.map(([section, contents]) => (
          <Paper
            variant="outlined"
            key={section}
            sx={{ mb: 2, p: 1.5, overflowX: "auto", maxWidth: "100%" }}
          >
            <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
              {humanizeEnum(section)}
            </Typography>
            {contents !== null && typeof contents === "object" ? (
              (Array.isArray(contents)
                ? contents.map((v, i) => [String(i), v] as [string, unknown])
                : Object.entries(contents as Record<string, unknown>)
              ).map(([k, v]) => (
                <ConfigNode key={k} label={k} value={v} depth={0} />
              ))
            ) : (
              <ScalarValue value={contents} />
            )}
          </Paper>
        ))
      )}
    </Box>
  );
};

export default EffectiveConfigPage;
