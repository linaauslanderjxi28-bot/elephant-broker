// API Keys settings page.
//
// Create (plaintext shown once), list masked, and revoke API keys. Keys are
// bound to the current actor server-side.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import { apiGet, apiSend, relativeTime } from "../home/dashboardApi";

interface KeyRecord {
  key_id?: string;
  id?: string;
  label?: string;
  key_prefix?: string;
  masked_key?: string;
  created_at?: string;
  revoked_at?: string | null;
}

function keyId(k: KeyRecord): string {
  return String(k.key_id ?? k.id ?? "");
}

export const ApiKeysPage: React.FC = () => {
  const [rows, setRows] = useState<KeyRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [plaintext, setPlaintext] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<any>("/auth/api-keys");
      setRows(Array.isArray(res) ? res : (res.keys ?? res.items ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    const res = await apiSend<any>("POST", "/auth/api-keys", { label });
    setPlaintext(res.key ?? res.plaintext ?? null);
    setCreateOpen(false);
    setLabel("");
    void load();
  };

  const revoke = async (id: string) => {
    if (!window.confirm("Revoke this key?")) return;
    await apiSend("DELETE", `/auth/api-keys/${id}`);
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
        <Typography variant="h5">API Keys</Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setCreateOpen(true)}
        >
          Create Key
        </Button>
      </Stack>
      <Alert severity="info" sx={{ mb: 2 }}>
        An API key grants the same access as your dashboard login. Store it
        securely.
      </Alert>
      {error && <Alert severity="error">{error}</Alert>}
      {!loading && (
        <Paper variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Label</TableCell>
                <TableCell>Key</TableCell>
                <TableCell>Created</TableCell>
                <TableCell>Status</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((k) => (
                <TableRow key={keyId(k)}>
                  <TableCell>{k.label}</TableCell>
                  <TableCell>
                    <code>
                      {k.masked_key ??
                        (k.key_prefix ? `${k.key_prefix}••••` : "••••")}
                    </code>
                  </TableCell>
                  <TableCell>{relativeTime(k.created_at)}</TableCell>
                  <TableCell>{k.revoked_at ? "revoked" : "active"}</TableCell>
                  <TableCell align="right">
                    {!k.revoked_at && (
                      <IconButton onClick={() => revoke(keyId(k))}>
                        <DeleteIcon />
                      </IconButton>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>No API keys.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} fullWidth>
        <DialogTitle>Create API key</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Label"
            sx={{ mt: 1 }}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!label} onClick={create}>
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!plaintext} onClose={() => setPlaintext(null)} fullWidth>
        <DialogTitle>Your new API key</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Copy this key now — it will not be shown again.
          </DialogContentText>
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              fullWidth
              value={plaintext ?? ""}
              InputProps={{ readOnly: true }}
            />
            <Tooltip title="Copy">
              <IconButton
                onClick={() =>
                  plaintext && navigator.clipboard?.writeText(plaintext)
                }
              >
                <ContentCopyIcon />
              </IconButton>
            </Tooltip>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPlaintext(null)}>Done</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ApiKeysPage;
