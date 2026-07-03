/**
 * labels.ts — shared identity / id display helpers.
 *
 * Raw runtime identifiers (UUIDs, `namespace:<uuid>` actor keys) leak into the
 * UI in several places (RC-9 / the "ids as display names" findings). These
 * helpers produce short, human-friendly forms without inventing data — the
 * authoritative identity resolution still happens server-side.
 */

/** Matches a canonical UUID (v4-ish; any hex variant). */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Short 8-character prefix of an id, for compact display (tables, chips).
 * Nullish input yields "".
 */
export function shortId(id?: string | null): string {
  if (!id) return "";
  return id.slice(0, 8);
}

/**
 * Friendly display name for an actor identifier.
 *
 * - `"dashboard:e6add471-...."` → `"dashboard:e6add471"` (namespace kept, uuid
 *   shortened to its 8-char prefix).
 * - a bare UUID → its 8-char prefix.
 * - an already-human name (`"Alice"`, `"manager_agent"`) → returned as-is.
 * - nullish / empty → "".
 */
export function actorDisplayName(name?: string | null): string {
  if (!name) return "";
  const trimmed = name.trim();
  if (!trimmed) return "";

  const colon = trimmed.indexOf(":");
  if (colon > -1) {
    const ns = trimmed.slice(0, colon);
    const rest = trimmed.slice(colon + 1);
    const shortRest = UUID_RE.test(rest) ? shortId(rest) : rest || trimmed;
    return ns ? `${ns}:${shortRest}` : shortRest;
  }

  if (UUID_RE.test(trimmed)) return shortId(trimmed);
  return trimmed;
}
