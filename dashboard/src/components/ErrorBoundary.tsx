/**
 * ErrorBoundary.tsx — top-level React error boundary (defense-in-depth for RC-3).
 *
 * A render-time crash in one view (e.g. an object accidentally reaching a
 * MenuItem's `children`) should degrade that view, not white-screen the whole
 * app. Wrapping the authenticated shell's `<Outlet/>` in this boundary keeps the
 * chrome (sidebar, header, gateway selector) alive and shows a recoverable
 * error panel. The real fix for RC-3 is projecting API objects to
 * `{ value, label }`; this boundary is the safety net beneath that.
 */

import React from "react";
import { Alert, AlertTitle, Box, Button } from "@mui/material";

import { errorMessage } from "../lib/errors";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Optional custom fallback; defaults to a MUI Alert with a Reload button. */
  fallback?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: unknown;
}

class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    // Surface the crash for observability; the UI shows a recoverable panel.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] render crash:", error, info?.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) return this.props.fallback;

      return (
        <Box sx={{ p: 3 }}>
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={this.handleReload}>
                Reload
              </Button>
            }
          >
            <AlertTitle>Something went wrong</AlertTitle>
            {errorMessage(this.state.error)}
          </Alert>
        </Box>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
