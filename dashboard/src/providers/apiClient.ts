/**
 * apiClient.ts — thin fetch wrapper for the ElephantBroker dashboard.
 *
 * Responsibilities:
 *  - Resolve the runtime base URL from Vite env (`VITE_EB_RUNTIME_URL`). An empty
 *    string means "same-origin" (production build served at `/ui/*`), so we use
 *    the nullish coalescing operator to preserve `""` and only fall back to the
 *    dev default when the var is genuinely undefined.
 *  - Always send `credentials: "include"` so the SuperTokens session cookie and
 *    anti-CSRF headers ride along. SuperTokens' `Session.init()` patches the
 *    global `fetch`, so this plain `fetch` transparently gains cookie + CSRF +
 *    automatic access-token refresh.
 *  - Throw a Refine-compatible `HttpError` ({ message, statusCode }) on non-2xx
 *    so the Refine data/auth providers can react (e.g. logout on 401).
 *  - Provide a small module-level "selected gateway" store, persisted to
 *    localStorage, that the data provider reads to scope every request and that
 *    `GatewaySelector` writes when the operator switches gateways.
 */

// --- Base URL resolution --------------------------------------------------

const RAW_BASE = (import.meta as any).env?.VITE_EB_RUNTIME_URL as
  | string
  | undefined;

/** Runtime base URL. "" => same-origin; undefined => dev default. */
export const API_URL: string = RAW_BASE ?? "http://localhost:8420";

// --- Error type -----------------------------------------------------------

/** Matches Refine's `HttpError` shape so `onError`/`checkError` can branch. */
export class HttpError extends Error {
  statusCode: number;
  body?: unknown;

  constructor(message: string, statusCode: number, body?: unknown) {
    super(message);
    this.name = "HttpError";
    this.statusCode = statusCode;
    this.body = body;
  }
}

// --- Selected-gateway store ----------------------------------------------

const GATEWAY_STORAGE_KEY = "eb:selected_gateway";
/** Broadcast when the selected gateway changes so views can refetch. */
export const GATEWAY_CHANGED_EVENT = "eb:gateway-changed";

let _selectedGateway = "";

// Hydrate from localStorage once at module load (guard for SSR/tests).
if (typeof window !== "undefined" && window.localStorage) {
  try {
    _selectedGateway = window.localStorage.getItem(GATEWAY_STORAGE_KEY) ?? "";
  } catch {
    _selectedGateway = "";
  }
}

/** Return the currently selected gateway_id ("" => runtime default). */
export function getSelectedGateway(): string {
  return _selectedGateway;
}

/**
 * Set the selected gateway_id, persist it, and broadcast a change event.
 * Passing "" selects the runtime default (or all-gateways view for admins).
 */
export function setSelectedGateway(gatewayId: string): void {
  _selectedGateway = gatewayId ?? "";
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      if (_selectedGateway) {
        window.localStorage.setItem(GATEWAY_STORAGE_KEY, _selectedGateway);
      } else {
        window.localStorage.removeItem(GATEWAY_STORAGE_KEY);
      }
    } catch {
      /* ignore storage failures (private mode, quota) */
    }
    window.dispatchEvent(
      new CustomEvent(GATEWAY_CHANGED_EVENT, { detail: _selectedGateway }),
    );
  }
}

// --- Core request helper --------------------------------------------------

export interface RequestOptions {
  method?: string;
  /** JSON-serialisable request body. Omit for GET. */
  body?: unknown;
  /** Extra query-string params. Values are stringified; nullish are skipped. */
  query?: Record<string, unknown>;
  /** Extra headers merged over the defaults. */
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, unknown>): string {
  // `path` may already be absolute (starts with http) — respect it.
  const base = path.startsWith("http") ? path : `${API_URL}${path}`;
  if (!query) return base;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    search.append(key, String(value));
  }
  const qs = search.toString();
  if (!qs) return base;
  return base.includes("?") ? `${base}&${qs}` : `${base}?${qs}`;
}

/**
 * Perform a request and return the parsed JSON body (or `undefined` for 204).
 * Throws `HttpError` on any non-2xx response.
 */
export async function request<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, query, headers, signal } = options;

  const init: RequestInit = {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    signal,
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, query), init);

  // Try to parse a JSON body regardless of status, for error detail.
  let parsed: unknown;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!response.ok) {
    const detail =
      (parsed &&
        typeof parsed === "object" &&
        ((parsed as any).detail || (parsed as any).error || (parsed as any).message)) ||
      response.statusText ||
      `Request failed (${response.status})`;
    throw new HttpError(String(detail), response.status, parsed);
  }

  return parsed as T;
}

export const apiClient = {
  get: <T = unknown>(path: string, query?: Record<string, unknown>) =>
    request<T>(path, { method: "GET", query }),
  post: <T = unknown>(path: string, body?: unknown, query?: Record<string, unknown>) =>
    request<T>(path, { method: "POST", body, query }),
  put: <T = unknown>(path: string, body?: unknown, query?: Record<string, unknown>) =>
    request<T>(path, { method: "PUT", body, query }),
  patch: <T = unknown>(path: string, body?: unknown, query?: Record<string, unknown>) =>
    request<T>(path, { method: "PATCH", body, query }),
  delete: <T = unknown>(path: string, query?: Record<string, unknown>) =>
    request<T>(path, { method: "DELETE", query }),
};

export default apiClient;
