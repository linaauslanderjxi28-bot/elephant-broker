/**
 * BrandLogo — the elephant.broker brand marks (from the "EB Logo - Monogram"
 * design, claude.ai/design project d43b37a7).
 *
 * Two marks:
 *  - <BrandSeal>  — circular monogram seal: navy disc, "eb" + a teal dot.
 *  - <BrandLogo>  — full lockup: seal + "elephant.broker" wordmark.
 *
 * Deviations from the design source, per owner direction: the dot uses the
 * existing app theme teal (palette.primary.main #0fb5a8, not the design's
 * #14B8A6), and the disc/wordmark use the theme navy (#14233a, not #111827)
 * so the brand sits on the same palette as the rest of the dashboard.
 * Typeface: Space Grotesk (loaded in index.html), per the design.
 */
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";

const BRAND_FONT =
  "'Space Grotesk', 'Inter', -apple-system, 'Segoe UI', sans-serif";

export interface BrandSealProps {
  /** Disc diameter in px (design reference sizes: 76 / 92). */
  size?: number;
  /** "dark" = navy disc for light surfaces (default); "light" = white disc for dark surfaces. */
  mode?: "dark" | "light";
}

/** Circular "eb." monogram seal. */
export function BrandSeal({ size = 40, mode = "dark" }: BrandSealProps) {
  const theme = useTheme();
  const navy = theme.palette.secondary.main;
  const teal = theme.palette.primary.main;
  const disc = mode === "dark" ? navy : "#ffffff";
  const glyph = mode === "dark" ? "#ffffff" : navy;
  // Design ratio: 31px type in a 76px disc.
  const fontSize = Math.round(size * (31 / 76));
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: "999px",
        backgroundColor: disc,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        userSelect: "none",
      }}
    >
      <Box
        component="span"
        sx={{
          fontFamily: BRAND_FONT,
          fontWeight: 700,
          letterSpacing: "-0.04em",
          fontSize: `${fontSize}px`,
          lineHeight: 1,
          color: glyph,
          // optical centering: the dot adds visual weight to the right
          transform: "translateX(-1px)",
        }}
      >
        eb<Box component="span" sx={{ color: teal }}>.</Box>
      </Box>
    </Box>
  );
}

export interface BrandLogoProps {
  /** Seal diameter in px; wordmark scales with it (design ratio 36/76). */
  size?: number;
  mode?: "dark" | "light";
  /** Render the seal only (no wordmark). */
  sealOnly?: boolean;
}

/** Full lockup: seal + "elephant.broker" wordmark. */
export default function BrandLogo({
  size = 40,
  mode = "dark",
  sealOnly = false,
}: BrandLogoProps) {
  const theme = useTheme();
  const navy = theme.palette.secondary.main;
  const teal = theme.palette.primary.main;
  const wordmark = mode === "dark" ? navy : "#ffffff";
  const wordmarkSize = Math.round(size * (36 / 76));
  if (sealOnly) return <BrandSeal size={size} mode={mode} />;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: `${Math.max(8, Math.round(size * (20 / 76)))}px`,
        userSelect: "none",
      }}
    >
      <BrandSeal size={size} mode={mode} />
      <Box
        component="span"
        sx={{
          fontFamily: BRAND_FONT,
          fontWeight: 600,
          letterSpacing: "-0.03em",
          fontSize: `${wordmarkSize}px`,
          lineHeight: 1,
          color: wordmark,
          whiteSpace: "nowrap",
        }}
      >
        elephant<Box component="span" sx={{ color: teal }}>.</Box>broker
      </Box>
    </Box>
  );
}
