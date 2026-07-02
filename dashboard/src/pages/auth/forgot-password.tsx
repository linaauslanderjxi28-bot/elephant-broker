// Forgot-password page (SuperTokens EmailPassword reset email).
//
// Sends a password-reset email via the SuperTokens SDK (lazy-loaded so the
// bundle imports cleanly when the recipe is unconfigured).

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

async function sendReset(email: string): Promise<{ ok: boolean; message?: string }> {
  try {
    const mod: any = await import(
      /* @vite-ignore */ "supertokens-auth-react/recipe/emailpassword"
    );
    const res = await mod.sendPasswordResetEmail({
      formFields: [{ id: "email", value: email }],
    });
    if (res.status === "OK") return { ok: true };
    return { ok: false, message: res.status };
  } catch (e) {
    return { ok: false, message: (e as Error).message };
  }
}

export const ForgotPasswordPage: React.FC = () => {
  const { push } = useNavigation();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const res = await sendReset(email);
    setBusy(false);
    if (res.ok) setSent(true);
    else setError(res.message ?? "Could not send reset email");
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
            Reset password
          </Typography>
          {sent ? (
            <Alert severity="success">
              If that email exists, a reset link has been sent.
            </Alert>
          ) : (
            <form onSubmit={submit}>
              <Stack spacing={2}>
                {error && <Alert severity="error">{error}</Alert>}
                <TextField
                  label="Email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <Button type="submit" variant="contained" disabled={busy}>
                  Send reset link
                </Button>
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

export default ForgotPasswordPage;
