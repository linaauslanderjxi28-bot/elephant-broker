// Effective Config viewer (authority >= 70).
//
// Read-only view of GET /dashboard/config/effective — resolved config with
// secrets masked and per-value source tags (env / yaml / default). Grouped by
// section with a raw-JSON accordion.

import React, { useCallback, useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableRow,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { apiGet, useAuthority } from "../home/dashboardApi";

function sourceColor(src: string) {
  switch (src) {
    case "env":
      return "primary";
    case "yaml":
      return "info";
    default:
      return "default";
  }
}

// Renders a section object. Values may be scalars, {value, source} objects,
// or nested dicts — handled defensively.
function SectionTable({ data }: { data: any }) {
  const entries = Object.entries(data ?? {});
  return (
    <Table size="small">
      <TableBody>
        {entries.map(([k, v]) => {
          let value: any = v;
          let source: string | undefined;
          if (v && typeof v === "object" && !Array.isArray(v) && "value" in (v as any)) {
            value = (v as any).value;
            source = (v as any).source;
          }
          if (value && typeof value === "object") {
            value = JSON.stringify(value);
          }
          return (
            <TableRow key={k}>
              <TableCell sx={{ width: "40%" }}>{k}</TableCell>
              <TableCell>{String(value)}</TableCell>
              <TableCell align="right" sx={{ width: 90 }}>
                {source && (
                  <Chip
                    size="small"
                    label={source}
                    color={sourceColor(source) as any}
                  />
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export const EffectiveConfigPage: React.FC = () => {
  const authority = useAuthority();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet("/dashboard/config/effective"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (authority < 70) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="warning">
          Effective config requires authority &ge; 70.
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Effective Config
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <CircularProgress />
      ) : data ? (
        <>
          {Object.entries(data).map(([section, contents]) => {
            if (!contents || typeof contents !== "object") return null;
            return (
              <Paper variant="outlined" key={section} sx={{ mb: 2 }}>
                <Typography variant="subtitle1" sx={{ p: 1.5, pb: 0 }}>
                  {section}
                </Typography>
                <SectionTable data={contents} />
              </Paper>
            );
          })}
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Raw JSON</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                {JSON.stringify(data, null, 2)}
              </pre>
            </AccordionDetails>
          </Accordion>
        </>
      ) : null}
    </Box>
  );
};

export default EffectiveConfigPage;
