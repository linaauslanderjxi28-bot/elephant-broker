// Register page (SuperTokens EmailPassword signup).
//
// Uses Refine's useRegister when available (delegates to authProvider), and
// falls back to a direct SuperTokens signUp call. The SuperTokens SDK is loaded
// lazily so the bundle imports cleanly even if the recipe is unconfigured.

import React, { useState } from "react";
import { useNavigation, useRegister } from "@refinedev/core";
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

async function supertokensSignUp(
  email: string,
  password: string,
): Promise<{ ok: boolean; message?: string }> {
  try {
    const mod: any = await import(
      /* @vite-ignore */ "supertokens-auth-react/recipe/emailpassword"
    );
    const res = await mod.signUp({
      formFields: [
        { id: "email", value: email },
        { id: "password", value: password },
      ],
    });
    if (res.status === "OK") return { ok: true };
    if (res.status === "FIELD_ERROR") {
      return {
        ok: false,
        message: res.formFields
          ?.map((f: any) => f.error)
          .filter(Boolean)
          .join(", "),
      };
    }
    return { ok: false, message: res.status };
  } catch (e) {
    return { ok: false, message: (e as Error).message };
  }
}

export const RegisterPage: React.FC = () => {
  const { push } = useNavigation();
  const { mutate: register } = useRegister();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    // Prefer the Refine auth provider path; fall back to a direct call.
    try {
      register({ email, password } as any, {
        onError: async () => {
          const res = await supertokensSignUp(email, password);
          if (res.ok) push("/");
          else setError(res.message ?? "Sign-up failed");
        },
        onSuccess: (data: any) => {
          if (data && data.success === false)
            setError(data.error?.message ?? "Sign-up failed");
          else push("/");
        },
      });
    } catch {
      const res = await supertokensSignUp(email, password);
      if (res.ok) push("/");
      else setError(res.message ?? "Sign-up failed");
    } finally {
      setBusy(false);
    }
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
          <Typography variant="h5" align="center" gutterBottom>
            Create account
          </Typography>
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
              <TextField
                label="Password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <Button type="submit" variant="contained" disabled={busy}>
                Sign up
              </Button>
              <Link
                component="button"
                type="button"
                variant="body2"
                onClick={() => push("/login")}
              >
                Back to sign in
              </Link>
            </Stack>
          </form>
        </CardContent>
      </Card>
    </Box>
  );
};

export default RegisterPage;
