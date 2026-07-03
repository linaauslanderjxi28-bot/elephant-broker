/**
 * apiNormalize.ts — shared coercion of loose API list payloads into stable
 * `{ value, label }` option lists for MUI Select / Autocomplete controls.
 *
 * These helpers exist because several EB runtime endpoints return list data in
 * more than one envelope shape (a bare array, `{ items }`, `{ gateways }`,
 * `{ sessions }`) and individual entries may be strings or objects. Mapping raw
 * API objects straight into MenuItem children is exactly what white-screens the
 * app (RC-3 — "Objects are not valid as a React child"), so every dropdown must
 * project through an explicit `(value, label)` projection. This module is the
 * single source of truth for that projection.
 */

/** A normalized option for a MUI Select / Autocomplete control. */
export interface SelectOption {
  value: string;
  label: string;
}

/**
 * Pull the underlying array out of a loose list payload. Accepts a bare array
 * or an object whose value lives under one of `keys` (first match wins).
 */
function extractArray(payload: unknown, keys: readonly string[]): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    for (const key of keys) {
      const value = obj[key];
      if (Array.isArray(value)) return value;
    }
  }
  return [];
}

/**
 * Coerce a `/dashboard/gateways` payload into a stable option list.
 *
 * Handles: a bare array, `{ gateways }`, or `{ items }`; entries may be plain
 * strings or objects carrying `gateway_id` / `id` / `name`. Entries that yield
 * no usable id are dropped. Mirrors the logic previously inlined in
 * GatewaySelector, promoted here so every gateway dropdown shares it.
 */
export function normalizeGateways(payload: unknown): SelectOption[] {
  const raw = extractArray(payload, ["gateways", "items"]);

  const options: SelectOption[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (!entry) continue;
      options.push({ value: entry, label: entry });
    } else if (entry && typeof entry === "object") {
      const obj = entry as Record<string, unknown>;
      const value = String(obj.gateway_id ?? obj.id ?? obj.name ?? "");
      if (!value) continue;
      const label = String(obj.name ?? obj.label ?? value);
      options.push({ value, label });
    }
  }
  return options;
}

/**
 * Coerce an active-sessions payload into a stable option list.
 *
 * Primary shape: `{ sessions: [{ session_key, session_id, event_count,
 * last_event_at }] }` (also tolerates a bare array or `{ items }`). The option
 * value is the stable `session_key`; the label appends the event count when the
 * backend supplies one (e.g. `agent:main:main · 42 events`).
 */
export function normalizeActiveSessions(payload: unknown): SelectOption[] {
  const raw = extractArray(payload, ["sessions", "items"]);

  const options: SelectOption[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (!entry) continue;
      options.push({ value: entry, label: entry });
      continue;
    }
    if (!entry || typeof entry !== "object") continue;

    const obj = entry as Record<string, unknown>;
    const value = String(obj.session_key ?? obj.session_id ?? obj.id ?? "");
    if (!value) continue;

    const count = obj.event_count;
    const hasCount = typeof count === "number" && Number.isFinite(count);
    const label = hasCount ? `${value} · ${count} events` : value;
    options.push({ value, label });
  }
  return options;
}

/**
 * Generic array → option projection. Callers pass explicit accessors so an API
 * object can never leak into a MenuItem's `children` (the RC-3 failure mode).
 * Nullish input yields an empty list; entries that project to an empty value
 * are dropped.
 */
export function toOptions<T>(
  arr: T[] | undefined | null,
  getValue: (t: T) => string,
  getLabel: (t: T) => string,
): SelectOption[] {
  if (!arr) return [];
  const options: SelectOption[] = [];
  for (const item of arr) {
    const value = getValue(item);
    if (!value) continue;
    options.push({ value, label: getLabel(item) });
  }
  return options;
}
