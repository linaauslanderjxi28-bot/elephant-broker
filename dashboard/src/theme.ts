/**
 * ElephantBroker dashboard MUI theme.
 *
 * Palette + typography are lifted from the agreed dashboard mockup
 * (`local/DASHBOARD-SCOPE-OF-WORK.html`): teal primary (#0fb5a8), dark-navy
 * secondary (#14233a), Inter type, hairline borders (#e4eaef). The design brief
 * is "non-technical-first": a calm, light surface with restrained accents.
 *
 * We expose BOTH a light and a dark theme built from one factory so the
 * "Theme" preference (settings-3) actually takes effect: `App.tsx` selects
 * `ebTheme` vs `ebThemeDark` from the persisted preference. The brand accents
 * (teal/navy) and the whole typography/shape/component surface are shared; only
 * the surface colours (background, paper, text, borders) flip per mode, sourced
 * from a single `surfaces` map so component overrides stay mode-correct instead
 * of hardcoding light-only colours.
 */
import { createTheme, type Theme } from "@mui/material/styles";

// --- Brand palette (from the mockup) ---
// Kept as the LIGHT-mode surface source and imported directly elsewhere
// (e.g. pages/memory/graph.tsx) for brand accent colours — do not change shape.
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

/** Mode-specific surface colours (background/paper/text/borders). */
interface Surfaces {
  bg: string;
  paper: string;
  textPrimary: string;
  textSecondary: string;
  border: string;
}

const LIGHT_SURFACES: Surfaces = {
  bg: palette.bg,
  paper: palette.paper,
  textPrimary: palette.textPrimary,
  textSecondary: palette.textSecondary,
  border: palette.border,
};

// Dark surfaces stay in the brand's navy family so the teal accent still reads
// and cards/appbar/drawer separate cleanly from the deep background.
const DARK_SURFACES: Surfaces = {
  bg: "#0f1a29",
  paper: "#16243a",
  textPrimary: "#e7edf3",
  textSecondary: "#9fb2c4",
  border: "#263548",
};

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

/**
 * Build the EB theme for a colour mode. Brand accents + typography + component
 * shape are identical across modes; only the `surfaces` differ.
 */
function createEbTheme(mode: "light" | "dark"): Theme {
  const surfaces = mode === "dark" ? DARK_SURFACES : LIGHT_SURFACES;

  return createTheme({
    palette: {
      mode,
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
        default: surfaces.bg,
        paper: surfaces.paper,
      },
      text: {
        primary: surfaces.textPrimary,
        secondary: surfaces.textSecondary,
      },
      divider: surfaces.border,
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
      subtitle2: { fontWeight: 500, color: surfaces.textSecondary },
      button: { fontWeight: 600, textTransform: "none" },
      body2: { color: surfaces.textSecondary },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: surfaces.bg,
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
          },
          outlined: {
            borderColor: surfaces.border,
          },
        },
      },
      MuiCard: {
        defaultProps: {
          variant: "outlined",
        },
        styleOverrides: {
          root: {
            borderColor: surfaces.border,
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
            backgroundColor: surfaces.paper,
            borderBottom: `1px solid ${surfaces.border}`,
            color: surfaces.textPrimary,
          },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: {
            borderRight: `1px solid ${surfaces.border}`,
            backgroundColor: surfaces.paper,
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
            borderColor: surfaces.border,
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
}

/** Light theme (default). */
export const ebTheme = createEbTheme("light");

/** Dark theme — same brand/typography, dark surfaces (settings-3). */
export const ebThemeDark = createEbTheme("dark");

export default ebTheme;
