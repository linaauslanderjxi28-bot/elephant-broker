// Register page (SuperTokens EmailPassword signup via the Refine auth provider).
//
// Uses Refine's useRegister, which delegates to authProvider.register
// (SuperTokens signUp under the hood) and, on success, redirects to the saved
// default page. Failures are surfaced ONCE via Refine's notification provider
// with a friendly title + normalized message (providers/authProvider +
// lib/errors); the page keeps no duplicate inline error and no second sign-up
// attempt (auth-4).

import React, { useState } from "react";
import { useNavigation, useRegister } from "@refinedev/core";
import {
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

export const RegisterPage: React.FC = () => {
  const { push } = useNavigation();
  const { mutate: register, isLoading } = useRegister();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // authProvider.register performs the SuperTokens sign-up and returns a
    // redirectTo; Refine handles the redirect and surfaces any failure once.
    register({ email, password });
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
            Create account
          </Typography>
          <form onSubmit={submit}>
            <Stack spacing={2}>
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
