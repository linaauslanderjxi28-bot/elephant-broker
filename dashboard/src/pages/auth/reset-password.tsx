// Reset-password page (SuperTokens EmailPassword "submit new password").
//
// This is the landing page for the link emailed by the forgot-password flow
// (fixes auth-2, which previously dead-ended on /login). The reset token is
// carried in the URL query string; SuperTokens' `submitNewPassword` reads it
// from `window.location` automatically, so we only collect the new password.
//
// The recipe module is lazy-loaded (matching forgot-password.tsx) so the bundle
// imports cleanly even when the EmailPassword recipe is unconfigured.

import React, { useState } from "react";
import { useNavigation } from "@refinedev/core";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import BrandLogo from "../../components/BrandLogo";

interface SubmitResult {
  ok: boolean;
  message?: string;
  invalidToken?: boolean;
}

async function submitNewPassword(password: string): Promise<SubmitResult> {
  try {
    const mod: any = await import(
      /* @vite-ignore */ "supertokens-auth-react/recipe/emailpassword"
    );
    // Token is read from the current URL by the SDK.
    const res = await mod.submitNewPassword({
      formFields: [{ id: "password", value: password }],
    });
    if (res.status === "OK") return { ok: true };
    if (res.status === "RESET_PASSWORD_INVALID_TOKEN_ERROR") {
      return {
        ok: false,
        invalidToken: true,
        message:
          "This reset link is invalid or has expired. Request a new one below.",
      };
    }
    if (res.status === "FIELD_ERROR") {
      const message = (res.formFields ?? [])
        .map((f: { id: string; error: string }) => f.error)
        .filter(Boolean)
        .join(" ");
      return { ok: false, message: message || "Please choose a stronger password." };
    }
    return { ok: false, message: String(res.status) };
  } catch (e) {
    return { ok: false, message: (e as Error).message };
  }
}

export const ResetPasswordPage: React.FC = () => {
  const { push } = useNavigation();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invalidToken, setInvalidToken] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInvalidToken(false);

    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    setBusy(true);
    const res = await submitNewPassword(password);
    setBusy(false);

    if (res.ok) {
      setDone(true);
      return;
    }
    setInvalidToken(Boolean(res.invalidToken));
    setError(res.message ?? "Could not reset password.");
  };

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Card sx={{ width: 380 }} variant="outlined">
        <CardContent>
          <Box sx={{ display: "flex", justifyContent: "center", my: 1.5 }}>
            <BrandLogo size={40} />
          </Box>
          <Typography variant="h5" align="center" gutterBottom>
            Choose a new password
          </Typography>
          {done ? (
            <Stack spacing={2}>
              <Alert severity="success">
                Your password has been reset. You can now sign in.
              </Alert>
              <Button variant="contained" onClick={() => push("/login")}>
                Go to sign in
              </Button>
            </Stack>
          ) : (
            <form onSubmit={submit}>
              <Stack spacing={2}>
                {error && <Alert severity="error">{error}</Alert>}
                <TextField
                  label="New password"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <TextField
                  label="Confirm new password"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
                <Button type="submit" variant="contained" disabled={busy}>
                  Reset password
                </Button>
                {invalidToken && (
                  <Button
                    type="button"
                    variant="text"
                    onClick={() => push("/forgot-password")}
                  >
                    Request a new reset link
                  </Button>
                )}
              </Stack>
            </form>
          )}
          <Link
            component="button"
            type="button"
            variant="body2"
            sx={{ mt: 2 }}
            onClick={() => push("/login")}
          >
            Back to sign in
          </Link>
        </CardContent>
      </Card>
    </Box>
  );
};

export default ResetPasswordPage;
