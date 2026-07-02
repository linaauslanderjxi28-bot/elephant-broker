// Login page (SuperTokens EmailPassword via the Refine auth provider).
//
// Uses Refine's useLogin, which delegates to authProvider.login (SuperTokens
// signIn under the hood). Degrades gracefully with an inline error message.

import React, { useState } from "react";
import { useLogin, useNavigation } from "@refinedev/core";
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

export const LoginPage: React.FC = () => {
  const { mutate: login, isLoading } = useLogin();
  const { push } = useNavigation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    login(
      { email, password },
      {
        onError: (err: any) =>
          setError(err?.message ?? "Sign-in failed"),
        onSuccess: (data: any) => {
          if (data && data.success === false) {
            setError(data.error?.message ?? "Sign-in failed");
          }
        },
      },
    );
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
            <BrandLogo size={44} />
          </Box>
          <Typography
            variant="body2"
            align="center"
            color="text.secondary"
            sx={{ mb: 2 }}
          >
            Sign in to the dashboard
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
              <Button type="submit" variant="contained" disabled={isLoading}>
                Sign in
              </Button>
              <Stack
                direction="row"
                justifyContent="space-between"
                sx={{ mt: 1 }}
              >
                <Link
                  component="button"
                  type="button"
                  variant="body2"
                  onClick={() => push("/register")}
                >
                  Create account
                </Link>
                <Link
                  component="button"
                  type="button"
                  variant="body2"
                  onClick={() => push("/forgot-password")}
                >
                  Forgot password?
                </Link>
              </Stack>
            </Stack>
          </form>
        </CardContent>
      </Card>
    </Box>
  );
};

export default LoginPage;
