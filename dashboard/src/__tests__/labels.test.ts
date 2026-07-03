/**
 * labels.test.ts — unit tests for the identity/id display helpers in
 * `src/lib/labels.ts` (RC-9 "ids as display names" fix on branch EB-FE).
 *
 * The module is pure — it performs no I/O (no network, storage, or SDK calls) —
 * so, unlike authProvider.test.ts, there is nothing to mock here. The tests
 * exercise the branch-new behavior directly: known id shapes (bare UUID,
 * `namespace:<uuid>`) are shortened, while unknown/already-human values fall
 * through to the raw (trimmed) value.
 */

import { describe, it, expect } from "vitest";

import { shortId, actorDisplayName } from "../lib/labels";

// A canonical UUID plus its expected 8-char display prefix, reused below.
const UUID = "e6add471-1234-4abc-8def-0123456789ab";
const UUID_SHORT = "e6add471";

describe("shortId", () => {
  it("returns the first 8 characters of a longer id", () => {
    expect(shortId(UUID)).toBe(UUID_SHORT);
    expect(shortId("0123456789")).toBe("01234567");
  });

  it("returns short ids unchanged (no padding, no throw)", () => {
    expect(shortId("abc")).toBe("abc");
    expect(shortId("01234567")).toBe("01234567");
  });

  it("maps nullish / empty input to an empty string", () => {
    expect(shortId(undefined)).toBe("");
    expect(shortId(null)).toBe("");
    expect(shortId("")).toBe("");
  });
});

describe("actorDisplayName — known id shapes are shortened", () => {
  it("shortens a bare UUID to its 8-char prefix", () => {
    expect(actorDisplayName(UUID)).toBe(UUID_SHORT);
  });

  it("is case-insensitive when recognizing a UUID", () => {
    expect(actorDisplayName(UUID.toUpperCase())).toBe(UUID_SHORT.toUpperCase());
  });

  it("keeps the namespace and shortens a `namespace:<uuid>` actor key", () => {
    expect(actorDisplayName(`dashboard:${UUID}`)).toBe(
      `dashboard:${UUID_SHORT}`,
    );
  });
});

describe("actorDisplayName — unknown / human values fall back to the raw value", () => {
  it("returns an already-human name as-is", () => {
    expect(actorDisplayName("Alice")).toBe("Alice");
    expect(actorDisplayName("manager_agent")).toBe("manager_agent");
  });

  it("keeps a `namespace:name` pair intact when the tail is not a UUID", () => {
    expect(actorDisplayName("agent:worker")).toBe("agent:worker");
  });

  it("only splits on the first colon, leaving extra segments untouched", () => {
    expect(actorDisplayName("gateway:agent:session")).toBe(
      "gateway:agent:session",
    );
  });

  it("drops an empty namespace, returning just the tail (non-uuid)", () => {
    expect(actorDisplayName(":worker")).toBe("worker");
  });

  it("drops an empty namespace but still shortens a uuid tail", () => {
    expect(actorDisplayName(`:${UUID}`)).toBe(UUID_SHORT);
  });

  it("leaves a hyphenated-but-not-full-UUID token as-is", () => {
    expect(actorDisplayName("e6add471-1234")).toBe("e6add471-1234");
  });
});

describe("actorDisplayName — nullish / whitespace normalization", () => {
  it("maps nullish / empty input to an empty string", () => {
    expect(actorDisplayName(undefined)).toBe("");
    expect(actorDisplayName(null)).toBe("");
    expect(actorDisplayName("")).toBe("");
  });

  it("treats a whitespace-only string as empty", () => {
    expect(actorDisplayName("   ")).toBe("");
  });

  it("trims surrounding whitespace off a human name", () => {
    expect(actorDisplayName("  Alice  ")).toBe("Alice");
  });
});
