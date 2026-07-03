/**
 * format.ts — shared display formatting for the dashboard.
 *
 * Every cell that shows an enum, a timestamp, a count, or a pluralized noun
 * should route through here so casing, relative-time phrasing, and number
 * compaction stay consistent app-wide (RC-12). No component should hand-roll
 * `snake_case` → Title-case or bespoke "3 minutes ago" logic again.
 */

import { formatDistanceToNow, parseISO, isValid } from "date-fns";

/**
 * Humanize an enum-ish token: `snake_case` / `SCREAMING_CASE` / `kebab-case`
 * → "Title Case". Empty / nullish input yields "".
 *
 * Examples: `"graph_completion"` → "Graph Completion",
 * `"GRAPH_COMPLETION"` → "Graph Completion", `"memory-search"` → "Memory Search".
 */
export function humanizeEnum(v?: string | null): string {
  if (!v) return "";
  return v
    .trim()
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/** Parse an incoming timestamp (ISO string, epoch ms/number-string) to a Date. */
function toDate(value: string | number): Date | null {
  if (typeof value === "number") {
    const d = new Date(value);
    return isValid(d) ? d : null;
  }
  const trimmed = value.trim();
  if (!trimmed) return null;

  // Prefer strict ISO parsing; fall back to the Date constructor for other
  // formats (RFC 2822, epoch-as-string, etc.).
  let d = parseISO(trimmed);
  if (isValid(d)) return d;

  if (/^-?\d+$/.test(trimmed)) {
    d = new Date(Number(trimmed));
    if (isValid(d)) return d;
  }

  d = new Date(trimmed);
  return isValid(d) ? d : null;
}

/**
 * Format a timestamp as a relative string with an absolute tooltip.
 *
 * Returns `{ text, title }` where `text` is e.g. "3 minutes ago" (suitable for
 * inline display) and `title` is the absolute local timestamp (suitable for a
 * `title=` tooltip). Missing or unparseable input yields `{ text: "—", title: "" }`.
 */
export function formatRelativeTime(
  iso?: string | null,
): { text: string; title: string } {
  if (iso == null || iso === "") return { text: "—", title: "" };

  const d = toDate(iso);
  if (!d) return { text: "—", title: "" };

  return {
    text: formatDistanceToNow(d, { addSuffix: true }),
    title: d.toLocaleString(),
  };
}

/**
 * Choose the singular or plural noun for a count. Returns only the noun (the
 * caller composes `${n} ${pluralize(n, ...)}`). Defaults the plural to
 * `singular + "s"` when not supplied.
 */
export function pluralize(n: number, singular: string, plural?: string): string {
  return Math.abs(n) === 1 ? singular : plural ?? `${singular}s`;
}

/**
 * Compact number formatting for counts (e.g. `1500` → "1.5K", `2_000_000` → "2M").
 * Nullish input renders as "0".
 */
export function formatCount(n?: number | null): string {
  if (n == null || !Number.isFinite(n)) return "0";
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}
