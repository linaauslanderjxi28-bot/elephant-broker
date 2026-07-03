/**
 * errors.ts — single normalization point for API / runtime errors.
 *
 * The EB runtime (FastAPI) surfaces failures in several shapes:
 *   - `{ detail: "human string" }`                        (HTTPException)
 *   - `{ detail: [{ loc, msg, type }, ...] }`             (422 validation)
 *   - occasionally `{ error }` / `{ message }`
 * and the dashboard's own `HttpError` (see providers/apiClient.ts) carries the
 * parsed body on `.body` plus a numeric `.statusCode`. Historically each call
 * site tried to stringify these ad-hoc, which produced the infamous
 * `"[object Object]"` toast (RC-4). This module parses every known shape into a
 * human message plus structured, field-keyed errors — and, as a hard guarantee,
 * NEVER returns `"[object Object]"`.
 */

/** Normalized error: a human message plus per-field messages (may be empty). */
export interface NormalizedError {
  message: string;
  /** Keyed by the last FastAPI `loc` segment (the field name), when available. */
  fieldErrors: Record<string, string>;
}

const GENERIC_MESSAGE = "Something went wrong. Please try again.";

/** True for stringifications that leaked an object and must never be shown. */
function isUselessMessage(msg: string): boolean {
  const trimmed = msg.trim();
  if (!trimmed) return true;
  // "[object Object]", "[object Object],[object Object]", etc.
  return /^(\[object \w+\])(,\s*\[object \w+\])*$/.test(trimmed);
}

/** Best-effort human string for an arbitrary value, never "[object Object]". */
function safeStringify(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    const json = JSON.stringify(value);
    if (json && json !== "{}" && json !== "[]") return json;
  } catch {
    /* fall through */
  }
  return "";
}

/**
 * Parse a FastAPI `detail` value (string or 422 validation array) into a
 * NormalizedError. Returns `null` if `detail` is not a recognized shape.
 */
function fromDetail(detail: unknown): NormalizedError | null {
  if (typeof detail === "string") {
    const message = detail.trim();
    return message ? { message, fieldErrors: {} } : null;
  }

  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    const messages: string[] = [];

    for (const item of detail) {
      if (typeof item === "string") {
        if (item.trim()) messages.push(item.trim());
        continue;
      }
      if (!item || typeof item !== "object") continue;

      const obj = item as Record<string, unknown>;
      const loc = Array.isArray(obj.loc) ? obj.loc : [];
      const rawMsg = obj.msg;
      const msg =
        typeof rawMsg === "string" ? rawMsg : safeStringify(rawMsg);
      if (!msg) continue;

      // Field key = last loc segment, skipping the generic request part
      // ("body" / "query" / "path") so forms key on the real field name.
      let key = "";
      for (let i = loc.length - 1; i >= 0; i--) {
        const seg = loc[i];
        if (seg == null) continue;
        const segStr = String(seg);
        if (i === 0 && ["body", "query", "path", "header", "cookie"].includes(segStr)) {
          continue;
        }
        key = segStr;
        break;
      }

      if (key && !(key in fieldErrors)) fieldErrors[key] = msg;
      messages.push(key ? `${key}: ${msg}` : msg);
    }

    const message = messages.join("; ").trim();
    return {
      message: message || "Validation failed.",
      fieldErrors,
    };
  }

  return null;
}

/**
 * Parse a response body (parsed JSON, or a raw string) into a NormalizedError.
 * Understands FastAPI `{ detail }` (string or 422 array), and `{ error }` /
 * `{ message }` fallbacks. Returns `null` when nothing usable is present.
 */
function fromBody(body: unknown): NormalizedError | null {
  if (body == null) return null;

  if (typeof body === "string") {
    const message = body.trim();
    return message && !isUselessMessage(message)
      ? { message, fieldErrors: {} }
      : null;
  }

  if (typeof body !== "object") return null;

  const obj = body as Record<string, unknown>;

  if ("detail" in obj) {
    const parsed = fromDetail(obj.detail);
    if (parsed) return parsed;
  }

  for (const key of ["error", "message"] as const) {
    const value = obj[key];
    if (typeof value === "string" && value.trim() && !isUselessMessage(value)) {
      return { message: value.trim(), fieldErrors: {} };
    }
    // Some backends nest FastAPI-style detail under `error`.
    if (value && typeof value === "object") {
      const nested = fromBody(value);
      if (nested) return nested;
    }
  }

  return null;
}

/**
 * Normalize any thrown value into `{ message, fieldErrors }`.
 *
 * Handled inputs, in priority order:
 *   1. Our `HttpError` (or anything with a `.body`): parse the structured body.
 *   2. A raw parsed response object carrying `detail` / `error` / `message`.
 *   3. A plain `Error` (use `.message`, unless it leaked an object).
 *   4. A bare string.
 *   5. Anything else: a safe JSON stringification, else a generic message.
 *
 * Guarantee: the returned `message` is never empty and never `"[object Object]"`.
 */
export function normalizeApiError(err: unknown): NormalizedError {
  if (err == null) {
    return { message: GENERIC_MESSAGE, fieldErrors: {} };
  }

  if (typeof err === "string") {
    const message = err.trim();
    return {
      message: message && !isUselessMessage(message) ? message : GENERIC_MESSAGE,
      fieldErrors: {},
    };
  }

  if (typeof err === "object") {
    const obj = err as Record<string, unknown>;

    // 1. HttpError / any carrier with a structured body.
    if ("body" in obj) {
      const fromErrBody = fromBody(obj.body);
      if (fromErrBody) return fromErrBody;
    }

    // 2. The value itself is (or wraps) a FastAPI response body.
    const direct = fromBody(err);
    if (direct) return direct;

    // 3. An Error / HttpError with a usable `.message`.
    const message = obj.message;
    if (typeof message === "string" && message.trim() && !isUselessMessage(message)) {
      return { message: message.trim(), fieldErrors: {} };
    }

    // 4. Last resort: a safe stringification that isn't "[object Object]".
    const safe = safeStringify(err);
    return {
      message: safe && !isUselessMessage(safe) ? safe : GENERIC_MESSAGE,
      fieldErrors: {},
    };
  }

  const safe = safeStringify(err);
  return {
    message: safe && !isUselessMessage(safe) ? safe : GENERIC_MESSAGE,
    fieldErrors: {},
  };
}

/** Convenience: the human-readable message for any thrown value. */
export function errorMessage(err: unknown): string {
  return normalizeApiError(err).message;
}
