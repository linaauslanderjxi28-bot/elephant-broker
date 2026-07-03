/**
 * apiNormalize.test.ts — unit tests for the loose-payload → `{ value, label }`
 * normalization helpers (branch EB-FE, new module `src/lib/apiNormalize.ts`).
 *
 * These helpers are pure functions with no I/O — the whole point is that they
 * reconcile the several envelope shapes EB runtime endpoints return (bare array,
 * `{ items }`, `{ gateways }`, `{ sessions }`) and coalesce per-entry id/label
 * fields, so nothing here mocks a client, store, or network. The tests exercise
 * envelope extraction, first-key-wins precedence, id coalescing, label
 * derivation, and the "drop unusable entries" contract.
 */

import { describe, it, expect, vi } from "vitest";

import {
  normalizeGateways,
  normalizeActiveSessions,
  toOptions,
} from "../lib/apiNormalize";

describe("normalizeGateways", () => {
  it("extracts a bare array of gateway objects", () => {
    const out = normalizeGateways([
      { gateway_id: "gw-1" },
      { gateway_id: "gw-2" },
    ]);
    expect(out).toEqual([
      { value: "gw-1", label: "gw-1" },
      { value: "gw-2", label: "gw-2" },
    ]);
  });

  it("unwraps the `{ gateways }` envelope", () => {
    const out = normalizeGateways({ gateways: [{ gateway_id: "gw-a" }] });
    expect(out).toEqual([{ value: "gw-a", label: "gw-a" }]);
  });

  it("unwraps the `{ items }` envelope", () => {
    const out = normalizeGateways({ items: [{ gateway_id: "gw-b" }] });
    expect(out).toEqual([{ value: "gw-b", label: "gw-b" }]);
  });

  it("prefers `gateways` over `items` when both are present (first key wins)", () => {
    const out = normalizeGateways({
      gateways: [{ gateway_id: "from-gateways" }],
      items: [{ gateway_id: "from-items" }],
    });
    expect(out).toEqual([{ value: "from-gateways", label: "from-gateways" }]);
  });

  it("falls through to the next key when an earlier key is not an array", () => {
    const out = normalizeGateways({
      gateways: "not-an-array",
      items: [{ gateway_id: "gw-fallback" }],
    });
    expect(out).toEqual([{ value: "gw-fallback", label: "gw-fallback" }]);
  });

  it("accepts plain string entries", () => {
    const out = normalizeGateways(["gw-str-1", "gw-str-2"]);
    expect(out).toEqual([
      { value: "gw-str-1", label: "gw-str-1" },
      { value: "gw-str-2", label: "gw-str-2" },
    ]);
  });

  it("coalesces the id from gateway_id → id → name", () => {
    expect(normalizeGateways([{ gateway_id: "A", id: "B", name: "C" }])).toEqual(
      [{ value: "A", label: "C" }],
    );
    expect(normalizeGateways([{ id: "B", name: "C" }])).toEqual([
      { value: "B", label: "C" },
    ]);
    expect(normalizeGateways([{ name: "C" }])).toEqual([
      { value: "C", label: "C" },
    ]);
  });

  it("derives the label from name → label → value", () => {
    // name wins over the value-derived fallback
    expect(normalizeGateways([{ id: "gw", name: "Pretty" }])).toEqual([
      { value: "gw", label: "Pretty" },
    ]);
    // label field used when name is absent
    expect(normalizeGateways([{ id: "gw", label: "Labelled" }])).toEqual([
      { value: "gw", label: "Labelled" },
    ]);
    // falls back to the value when neither name nor label are present
    expect(normalizeGateways([{ id: "gw" }])).toEqual([
      { value: "gw", label: "gw" },
    ]);
  });

  it("stringifies numeric ids", () => {
    expect(normalizeGateways([{ id: 42 }])).toEqual([
      { value: "42", label: "42" },
    ]);
  });

  it("drops empty strings, entries with no usable id, and non-object primitives", () => {
    const out = normalizeGateways([
      "",
      { label: "no-id-here" },
      { gateway_id: "" },
      42,
      null,
      { gateway_id: "keep-me" },
    ]);
    expect(out).toEqual([{ value: "keep-me", label: "keep-me" }]);
  });

  it("returns an empty list for nullish / non-list payloads", () => {
    expect(normalizeGateways(null)).toEqual([]);
    expect(normalizeGateways(undefined)).toEqual([]);
    expect(normalizeGateways("string")).toEqual([]);
    expect(normalizeGateways({ other: [{ gateway_id: "x" }] })).toEqual([]);
  });
});

describe("normalizeActiveSessions", () => {
  it("uses session_key as the value and appends the event count to the label", () => {
    const out = normalizeActiveSessions({
      sessions: [
        { session_key: "agent:main:main", event_count: 42 },
        { session_key: "agent:sub:1", event_count: 3 },
      ],
    });
    expect(out).toEqual([
      { value: "agent:main:main", label: "agent:main:main · 42 events" },
      { value: "agent:sub:1", label: "agent:sub:1 · 3 events" },
    ]);
  });

  it("omits the count suffix when event_count is absent", () => {
    const out = normalizeActiveSessions({
      sessions: [{ session_key: "agent:main:main" }],
    });
    expect(out).toEqual([
      { value: "agent:main:main", label: "agent:main:main" },
    ]);
  });

  it("keeps a zero event_count in the label (0 is a valid finite count)", () => {
    const out = normalizeActiveSessions({
      sessions: [{ session_key: "sk", event_count: 0 }],
    });
    expect(out).toEqual([{ value: "sk", label: "sk · 0 events" }]);
  });

  it("ignores a non-finite / non-numeric event_count", () => {
    const out = normalizeActiveSessions({
      sessions: [
        { session_key: "nan", event_count: Number.NaN },
        { session_key: "str", event_count: "5" },
      ],
    });
    expect(out).toEqual([
      { value: "nan", label: "nan" },
      { value: "str", label: "str" },
    ]);
  });

  it("coalesces the value from session_key → session_id → id", () => {
    expect(
      normalizeActiveSessions([{ session_id: "sid", id: "iid" }]),
    ).toEqual([{ value: "sid", label: "sid" }]);
    expect(normalizeActiveSessions([{ id: "iid" }])).toEqual([
      { value: "iid", label: "iid" },
    ]);
  });

  it("tolerates a bare array, the `{ items }` envelope, and string entries", () => {
    expect(
      normalizeActiveSessions([{ session_key: "sk-bare" }]),
    ).toEqual([{ value: "sk-bare", label: "sk-bare" }]);
    expect(
      normalizeActiveSessions({ items: [{ session_key: "sk-items" }] }),
    ).toEqual([{ value: "sk-items", label: "sk-items" }]);
    expect(normalizeActiveSessions(["sk-string"])).toEqual([
      { value: "sk-string", label: "sk-string" },
    ]);
  });

  it("drops empty strings, entries with no usable value, and non-object primitives", () => {
    const out = normalizeActiveSessions([
      "",
      { event_count: 9 },
      7,
      null,
      { session_key: "keep" },
    ]);
    expect(out).toEqual([{ value: "keep", label: "keep" }]);
  });

  it("returns an empty list for nullish / non-list payloads", () => {
    expect(normalizeActiveSessions(null)).toEqual([]);
    expect(normalizeActiveSessions(undefined)).toEqual([]);
    expect(normalizeActiveSessions({ other: [] })).toEqual([]);
  });
});

describe("toOptions", () => {
  it("projects entries through the caller-supplied value/label accessors", () => {
    const out = toOptions(
      [
        { code: "c1", title: "First" },
        { code: "c2", title: "Second" },
      ],
      (t) => t.code,
      (t) => t.title,
    );
    expect(out).toEqual([
      { value: "c1", label: "First" },
      { value: "c2", label: "Second" },
    ]);
  });

  it("returns an empty list for null / undefined input", () => {
    expect(toOptions(null, String, String)).toEqual([]);
    expect(toOptions(undefined, String, String)).toEqual([]);
  });

  it("drops entries whose projected value is empty and never labels them", () => {
    const getLabel = vi.fn((t: { v: string; l: string }) => t.l);
    const out = toOptions(
      [
        { v: "", l: "dropped" },
        { v: "kept", l: "Kept" },
      ],
      (t) => t.v,
      getLabel,
    );
    expect(out).toEqual([{ value: "kept", label: "Kept" }]);
    // getLabel must not be invoked for the dropped (empty-value) entry
    expect(getLabel).toHaveBeenCalledTimes(1);
    expect(getLabel).toHaveBeenCalledWith({ v: "kept", l: "Kept" });
  });
});
