/**
 * theme.test.ts — unit tests for the branch-new `createEbTheme(mode)` factory in
 * `src/theme.ts`. The module is pure (it only calls MUI's `createTheme`), so
 * there is no I/O to mock. These tests pin the NEW behaviour: `ebTheme` (light)
 * and `ebThemeDark` (dark) must differ only in `palette.mode` + the surface
 * colours (background / paper / text / divider) while sharing the brand palette
 * (teal/navy), typography, and shape — and the standalone `palette` export must
 * stay intact for direct importers (e.g. pages/memory/graph.tsx).
 */

import { describe, it, expect } from "vitest";

import ebThemeDefault, { ebTheme, ebThemeDark, palette } from "../theme";

describe("palette export (brand accent source — unchanged shape)", () => {
  it("keeps the documented brand hexes", () => {
    expect(palette.teal).toBe("#0fb5a8");
    expect(palette.tealDark).toBe("#0b8076");
    expect(palette.tealTint).toBe("#e6f7f5");
    expect(palette.navy).toBe("#14233a");
    expect(palette.navyDeep).toBe("#11202c");
  });

  it("keeps the light-mode surface + status hexes importers rely on", () => {
    expect(palette.border).toBe("#e4eaef");
    expect(palette.borderStrong).toBe("#d6dee7");
    expect(palette.bg).toBe("#f1f5f7");
    expect(palette.paper).toBe("#ffffff");
    expect(palette.textPrimary).toBe("#14233a");
    expect(palette.textSecondary).toBe("#5b6b7c");
    expect(palette.textMuted).toBe("#8a99a8");
    expect(palette.info).toBe("#3b82f6");
    expect(palette.warning).toBe("#f59e0b");
    expect(palette.error).toBe("#e5484d");
    expect(palette.success).toBe("#0fb5a8");
  });
});

describe("palette.mode differs per theme", () => {
  it("ebTheme is light", () => {
    expect(ebTheme.palette.mode).toBe("light");
  });

  it("ebThemeDark is dark", () => {
    expect(ebThemeDark.palette.mode).toBe("dark");
  });
});

describe("default export is the light theme", () => {
  it("default === ebTheme", () => {
    expect(ebThemeDefault).toBe(ebTheme);
  });

  it("default export is light mode", () => {
    expect(ebThemeDefault.palette.mode).toBe("light");
  });
});

describe("surface colours flip per mode", () => {
  it("light theme uses LIGHT_SURFACES", () => {
    expect(ebTheme.palette.background.default).toBe("#f1f5f7");
    expect(ebTheme.palette.background.paper).toBe("#ffffff");
    expect(ebTheme.palette.text.primary).toBe("#14233a");
    expect(ebTheme.palette.text.secondary).toBe("#5b6b7c");
    expect(ebTheme.palette.divider).toBe("#e4eaef");
  });

  it("dark theme uses DARK_SURFACES", () => {
    expect(ebThemeDark.palette.background.default).toBe("#0f1a29");
    expect(ebThemeDark.palette.background.paper).toBe("#16243a");
    expect(ebThemeDark.palette.text.primary).toBe("#e7edf3");
    expect(ebThemeDark.palette.text.secondary).toBe("#9fb2c4");
    expect(ebThemeDark.palette.divider).toBe("#263548");
  });

  it("every surface slot actually differs between the two themes", () => {
    expect(ebTheme.palette.background.default).not.toBe(
      ebThemeDark.palette.background.default,
    );
    expect(ebTheme.palette.background.paper).not.toBe(
      ebThemeDark.palette.background.paper,
    );
    expect(ebTheme.palette.text.primary).not.toBe(
      ebThemeDark.palette.text.primary,
    );
    expect(ebTheme.palette.text.secondary).not.toBe(
      ebThemeDark.palette.text.secondary,
    );
    expect(ebTheme.palette.divider).not.toBe(ebThemeDark.palette.divider);
  });
});

describe("brand palette is shared across modes", () => {
  it("primary (teal) is identical in both themes", () => {
    expect(ebTheme.palette.primary.main).toBe(palette.teal);
    expect(ebThemeDark.palette.primary.main).toBe(palette.teal);
    expect(ebTheme.palette.primary.dark).toBe(palette.tealDark);
    expect(ebThemeDark.palette.primary.dark).toBe(palette.tealDark);
    expect(ebTheme.palette.primary.light).toBe(palette.tealTint);
    expect(ebThemeDark.palette.primary.light).toBe(palette.tealTint);
    expect(ebTheme.palette.primary.contrastText).toBe("#ffffff");
    expect(ebThemeDark.palette.primary.contrastText).toBe("#ffffff");
  });

  it("secondary (navy) is identical in both themes", () => {
    expect(ebTheme.palette.secondary.main).toBe(palette.navy);
    expect(ebThemeDark.palette.secondary.main).toBe(palette.navy);
    expect(ebTheme.palette.secondary.dark).toBe(palette.navyDeep);
    expect(ebThemeDark.palette.secondary.dark).toBe(palette.navyDeep);
  });

  it("status colours (info/warning/error/success) are identical in both themes", () => {
    expect(ebTheme.palette.info.main).toBe(palette.info);
    expect(ebThemeDark.palette.info.main).toBe(palette.info);
    expect(ebTheme.palette.warning.main).toBe(palette.warning);
    expect(ebThemeDark.palette.warning.main).toBe(palette.warning);
    expect(ebTheme.palette.error.main).toBe(palette.error);
    expect(ebThemeDark.palette.error.main).toBe(palette.error);
    expect(ebTheme.palette.success.main).toBe(palette.success);
    expect(ebThemeDark.palette.success.main).toBe(palette.success);
  });
});

describe("typography + shape are shared across modes", () => {
  it("shape.borderRadius is 10 in both themes", () => {
    expect(ebTheme.shape.borderRadius).toBe(10);
    expect(ebThemeDark.shape.borderRadius).toBe(10);
  });

  it("fontFamily is the Inter-led stack in both themes", () => {
    expect(ebTheme.typography.fontFamily).toBe(ebThemeDark.typography.fontFamily);
    expect(ebTheme.typography.fontFamily).toContain("Inter");
    expect(ebTheme.typography.fontFamily).toContain("sans-serif");
  });

  it("heading weights/sizes match across modes", () => {
    expect(ebTheme.typography.h1.fontWeight).toBe(700);
    expect(ebThemeDark.typography.h1.fontWeight).toBe(700);
    expect(ebTheme.typography.h1.fontSize).toBe("2rem");
    expect(ebThemeDark.typography.h1.fontSize).toBe("2rem");
    expect(ebTheme.typography.button.textTransform).toBe("none");
    expect(ebThemeDark.typography.button.textTransform).toBe("none");
  });

  it("subtitle2 colour tracks the mode surface (light navy vs dark slate)", () => {
    expect(ebTheme.typography.subtitle2.color).toBe(palette.textSecondary);
    expect(ebThemeDark.typography.subtitle2.color).toBe("#9fb2c4");
    expect(ebTheme.typography.subtitle2.color).not.toBe(
      ebThemeDark.typography.subtitle2.color,
    );
  });
});

describe("component overrides stay mode-correct (no hardcoded light colours)", () => {
  it("MuiAppBar background/border/text follow the mode surfaces", () => {
    const light = ebTheme.components?.MuiAppBar?.styleOverrides?.root as Record<
      string,
      unknown
    >;
    const dark = ebThemeDark.components?.MuiAppBar?.styleOverrides
      ?.root as Record<string, unknown>;

    expect(light.backgroundColor).toBe("#ffffff");
    expect(dark.backgroundColor).toBe("#16243a");
    expect(light.borderBottom).toBe("1px solid #e4eaef");
    expect(dark.borderBottom).toBe("1px solid #263548");
    expect(light.color).toBe("#14233a");
    expect(dark.color).toBe("#e7edf3");
  });

  it("MuiDrawer paper border/background follow the mode surfaces", () => {
    const light = ebTheme.components?.MuiDrawer?.styleOverrides?.paper as Record<
      string,
      unknown
    >;
    const dark = ebThemeDark.components?.MuiDrawer?.styleOverrides
      ?.paper as Record<string, unknown>;

    expect(light.backgroundColor).toBe("#ffffff");
    expect(dark.backgroundColor).toBe("#16243a");
    expect(light.borderRight).toBe("1px solid #e4eaef");
    expect(dark.borderRight).toBe("1px solid #263548");
  });

  it("MuiTooltip stays brand-navy in BOTH modes (constant, not surface-driven)", () => {
    const light = ebTheme.components?.MuiTooltip?.styleOverrides
      ?.tooltip as Record<string, unknown>;
    const dark = ebThemeDark.components?.MuiTooltip?.styleOverrides
      ?.tooltip as Record<string, unknown>;

    expect(light.backgroundColor).toBe(palette.navy);
    expect(dark.backgroundColor).toBe(palette.navy);
  });
});
