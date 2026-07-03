/**
 * errorsLib.test.ts — unit tests for lib/errors.ts (branch-new module).
 *
 * `lib/errors` is the single normalization point that turns every FastAPI /
 * runtime error shape into `{ message, fieldErrors }`. It is a pure module with
 * NO I/O — nothing to mock — so these tests exercise the shape→string mapping
 * directly: HTTPException `{ detail }`, 422 validation arrays (incl. the `loc`
 * field-key aggregation), `{ error }` / `{ message }` fallbacks, our HttpError
 * `.body` carrier, and the hard "never `[object Object]`, never empty" guarantee.
 *
 * NOTE: the auth-6 field-id-prefix stripping lives in providers/authProvider's
 * private `fieldErrorMessage()` (SuperTokens FIELD_ERROR path) and is already
 * covered by authProvider.test.ts; it is intentionally not duplicated here.
 */

import { describe, it, expect } from "vitest";

import { errorMessage, normalizeApiError } from "../lib/errors";

const GENERIC = "Something went wrong. Please try again.";

/** Mirrors providers/apiClient's HttpError: a structured `.body` + statusCode. */
class HttpError extends Error {
  statusCode: number;
  body: unknown;
  constructor(message: string, statusCode: number, body?: unknown) {
    super(message);
    this.name = "HttpError";
    this.statusCode = statusCode;
    this.body = body;
  }
}

describe("normalizeApiError — FastAPI { detail } string (HTTPException)", () => {
  it("uses the detail string as the message", () => {
    const result = normalizeApiError({ detail: "Actor not found" });
    expect(result).toEqual({ message: "Actor not found", fieldErrors: {} });
  });

  it("trims surrounding whitespace on the detail string", () => {
    expect(errorMessage({ detail: "  spaced out  " })).toBe("spaced out");
  });
});

describe("normalizeApiError — 422 validation array (field aggregation)", () => {
  it("keys fieldErrors on the last loc segment and joins the messages", () => {
    const result = normalizeApiError({
      detail: [
        { loc: ["body", "email"], msg: "field required", type: "missing" },
        { loc: ["body", "password"], msg: "too short", type: "too_short" },
      ],
    });
    expect(result.fieldErrors).toEqual({
      email: "field required",
      password: "too short",
    });
    expect(result.message).toBe(
      "email: field required; password: too short",
    );
  });

  it("skips the generic request part ('body') when it is the only loc segment", () => {
    // loc === ["body"] → no field name → message carries no "field: " prefix.
    const result = normalizeApiError({
      detail: [{ loc: ["body"], msg: "value is not a valid dict", type: "x" }],
    });
    expect(result.fieldErrors).toEqual({});
    expect(result.message).toBe("value is not a valid dict");
  });

  it("keys on the field for query/path locs too", () => {
    const result = normalizeApiError({
      detail: [{ loc: ["query", "limit"], msg: "must be positive", type: "x" }],
    });
    expect(result.fieldErrors).toEqual({ limit: "must be positive" });
    expect(result.message).toBe("limit: must be positive");
  });

  it("keeps the FIRST message for a duplicated field key, but lists both in the message", () => {
    const result = normalizeApiError({
      detail: [
        { loc: ["body", "email"], msg: "first", type: "x" },
        { loc: ["body", "email"], msg: "second", type: "x" },
      ],
    });
    expect(result.fieldErrors).toEqual({ email: "first" });
    expect(result.message).toBe("email: first; email: second");
  });

  it("tolerates bare strings interleaved in the detail array", () => {
    const result = normalizeApiError({
      detail: ["top-level note", { loc: ["body", "x"], msg: "bad", type: "y" }],
    });
    expect(result.fieldErrors).toEqual({ x: "bad" });
    expect(result.message).toBe("top-level note; x: bad");
  });

  it("falls back to 'Validation failed.' for an empty detail array", () => {
    const result = normalizeApiError({ detail: [] });
    expect(result).toEqual({ message: "Validation failed.", fieldErrors: {} });
  });
});

describe("normalizeApiError — { error } / { message } fallbacks", () => {
  it("uses a top-level { error } string", () => {
    expect(errorMessage({ error: "Server exploded" })).toBe("Server exploded");
  });

  it("uses a top-level { message } string", () => {
    expect(errorMessage({ message: "plain message" })).toBe("plain message");
  });

  it("unwraps a FastAPI detail nested under { error }", () => {
    const result = normalizeApiError({ error: { detail: "nested detail" } });
    expect(result).toEqual({ message: "nested detail", fieldErrors: {} });
  });
});

describe("normalizeApiError — HttpError carrier (.body wins over .message)", () => {
  it("parses the structured body of an HttpError, ignoring its generic message", () => {
    const err = new HttpError("HTTP 422", 422, {
      detail: [{ loc: ["body", "email"], msg: "required", type: "missing" }],
    });
    const result = normalizeApiError(err);
    expect(result.fieldErrors).toEqual({ email: "required" });
    expect(result.message).toBe("email: required");
  });

  it("falls back to the Error .message when the body is unusable", () => {
    const err = new HttpError("Bad gateway", 502, null);
    expect(errorMessage(err)).toBe("Bad gateway");
  });
});

describe("normalizeApiError — plain Error and bare string", () => {
  it("uses a plain Error's message", () => {
    expect(errorMessage(new Error("network down"))).toBe("network down");
  });

  it("passes a bare non-empty string through", () => {
    expect(errorMessage("just text")).toBe("just text");
  });
});

describe("normalizeApiError — the never-[object Object]/never-empty guarantee", () => {
  it("returns the generic message for a literal '[object Object]' string", () => {
    expect(errorMessage("[object Object]")).toBe(GENERIC);
  });

  it("returns the generic message for a comma-joined object leak", () => {
    expect(errorMessage("[object Object],[object Object]")).toBe(GENERIC);
  });

  it("returns the generic message for null and undefined", () => {
    expect(errorMessage(null)).toBe(GENERIC);
    expect(errorMessage(undefined)).toBe(GENERIC);
  });

  it("returns the generic message for an empty object with no recognized fields", () => {
    expect(errorMessage({})).toBe(GENERIC);
  });

  it("safely stringifies an unrecognized object rather than leaking [object Object]", () => {
    const msg = errorMessage({ foo: "bar" });
    expect(msg).toBe('{"foo":"bar"}');
    expect(msg).not.toBe("[object Object]");
  });

  it("stringifies a primitive (number) instead of returning empty", () => {
    expect(errorMessage(42)).toBe("42");
  });

  it("never returns '[object Object]' across a spread of odd inputs", () => {
    const inputs: unknown[] = [
      null,
      undefined,
      {},
      { detail: {} },
      { detail: 123 },
      new HttpError("x", 500, "[object Object]"),
      { message: "[object Object]" },
    ];
    for (const input of inputs) {
      const msg = errorMessage(input);
      expect(msg).not.toBe("[object Object]");
      expect(msg.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("errorMessage — thin wrapper over normalizeApiError().message", () => {
  it("returns exactly the normalized message", () => {
    const err = { detail: "same string" };
    expect(errorMessage(err)).toBe(normalizeApiError(err).message);
  });
});
