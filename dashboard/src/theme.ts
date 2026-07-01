/**
 * ElephantBroker dashboard MUI theme.
 *
 * Palette + typography are lifted from the agreed dashboard mockup
 * (`local/DASHBOARD-SCOPE-OF-WORK.html`): teal primary (#0fb5a8), dark-navy
 * secondary (#14233a), Inter type, hairline borders (#e4eaef). The design brief
 * is "non-technical-first": a calm, light surface with restrained accents.
 */
import { createTheme } from "@mui/material/styles";

// --- Brand palette (from the mockup) ---
export const palette = {
  teal: "#0fb5a8",
  tealDark: "#0b8076",
  tealTint: "#e6f7f5",
  navy: "#14233a",
  navyDeep: "#11202c",
  border: "#e4eaef",
  borderStrong: "#d6dee7",
  bg: "#f1f5f7",
  paper: "#ffffff",
  textPrimary: "#14233a",
  textSecondary: "#5b6b7c",
  textMuted: "#8a99a8",
  info: "#3b82f6",
  warning: "#f59e0b",
  error: "#e5484d",
  success: "#0fb5a8",
} as const;

const FONT_STACK = [
  "Inter",
  "-apple-system",
  "BlinkMacSystemFont",
  '"Segoe UI"',
  "Roboto",
  '"Helvetica Neue"',
  "Arial",
  "sans-serif",
].join(",");

export const ebTheme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: palette.teal,
      dark: palette.tealDark,
      light: palette.tealTint,
      contrastText: "#ffffff",
    },
    secondary: {
      main: palette.navy,
      dark: palette.navyDeep,
      contrastText: "#ffffff",
    },
    info: { main: palette.info },
    warning: { main: palette.warning },
    error: { main: palette.error },
    success: { main: palette.success },
    background: {
      default: palette.bg,
      paper: palette.paper,
    },
    text: {
      primary: palette.textPrimary,
      secondary: palette.textSecondary,
    },
    divider: palette.border,
  },
  shape: {
    borderRadius: 10,
  },
  typography: {
    fontFamily: FONT_STACK,
    h1: { fontWeight: 700, fontSize: "2rem" },
    h2: { fontWeight: 700, fontSize: "1.6rem" },
    h3: { fontWeight: 600, fontSize: "1.35rem" },
    h4: { fontWeight: 600, fontSize: "1.15rem" },
    h5: { fontWeight: 600, fontSize: "1rem" },
    h6: { fontWeight: 600, fontSize: "0.95rem" },
    subtitle1: { fontWeight: 500 },
    subtitle2: { fontWeight: 500, color: palette.textSecondary },
    button: { fontWeight: 600, textTransform: "none" },
    body2: { color: palette.textSecondary },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: palette.bg,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
        },
        outlined: {
          borderColor: palette.border,
        },
      },
    },
    MuiCard: {
      defaultProps: {
        variant: "outlined",
      },
      styleOverrides: {
        root: {
          borderColor: palette.border,
          borderRadius: 12,
        },
      },
    },
    MuiAppBar: {
      defaultProps: {
        color: "default",
        elevation: 0,
      },
      styleOverrides: {
        root: {
          backgroundColor: palette.paper,
          borderBottom: `1px solid ${palette.border}`,
          color: palette.textPrimary,
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          borderRight: `1px solid ${palette.border}`,
          backgroundColor: palette.paper,
        },
      },
    },
    MuiButton: {
      defaultProps: {
        disableElevation: true,
      },
      styleOverrides: {
        root: {
          borderRadius: 8,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 500,
          borderRadius: 6,
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderColor: palette.border,
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: palette.navy,
          fontSize: "0.75rem",
        },
      },
    },
  },
});

export default ebTheme;
