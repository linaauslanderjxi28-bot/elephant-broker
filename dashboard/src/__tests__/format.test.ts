/**
 * format.test.ts — unit tests for the shared display formatters in
 * `src/lib/format.ts` (branch-new module, RC-12).
 *
 * These are pure functions with no I/O: `humanizeEnum`, `pluralize`, and
 * `formatCount` are fully deterministic, and `formatRelativeTime` depends only
 * on `date-fns` (a pure computation lib) plus the wall clock. The relative-time
 * cases pin the clock with Vitest's fake timers so "N minutes ago" phrasing is
 * stable, and assert the tooltip against a locally-computed `toLocaleString()`
 * so the suite stays locale/timezone independent. Nothing here touches the
 * network, storage, or any SDK.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import {
  humanizeEnum,
  formatRelativeTime,
  pluralize,
  formatCount,
} from "../lib/format";

// The sentinel the module renders for missing/unparseable timestamps (U+2014).
const EM_DASH = "—";

describe("humanizeEnum", () => {
  it("returns empty string for nullish / empty input", () => {
    expect(humanizeEnum(undefined)).toBe("");
    expect(humanizeEnum(null)).toBe("");
    expect(humanizeEnum("")).toBe("");
  });

  it("title-cases snake_case tokens", () => {
    expect(humanizeEnum("graph_completion")).toBe("Graph Completion");
  });

  it("title-cases SCREAMING_CASE tokens (lowercasing the tail)", () => {
    expect(humanizeEnum("GRAPH_COMPLETION")).toBe("Graph Completion");
  });

  it("title-cases kebab-case tokens", () => {
    expect(humanizeEnum("memory-search")).toBe("Memory Search");
    expect(humanizeEnum("before-reset")).toBe("Before Reset");
  });

  it("lowercases the tail of a single all-caps word", () => {
    expect(humanizeEnum("CHUNKS")).toBe("Chunks");
  });

  it("collapses repeated separators and trims surrounding whitespace", () => {
    expect(humanizeEnum("  turn__ingest  ")).toBe("Turn Ingest");
  });

  it("drops empty segments from leading/trailing separators", () => {
    expect(humanizeEnum("_foo-bar_")).toBe("Foo Bar");
  });

  it("splits on mixed whitespace and underscore separators", () => {
    expect(humanizeEnum("session start hook")).toBe("Session Start Hook");
  });
});

describe("pluralize", () => {
  it("returns the singular only for a magnitude of exactly 1", () => {
    expect(pluralize(1, "fact")).toBe("fact");
  });

  it("defaults the plural to singular + 's' for 0 and n>1", () => {
    expect(pluralize(0, "fact")).toBe("facts");
    expect(pluralize(2, "fact")).toBe("facts");
    expect(pluralize(42, "fact")).toBe("facts");
  });

  it("uses the magnitude so -1 is singular", () => {
    expect(pluralize(-1, "fact")).toBe("fact");
  });

  it("treats other negatives as plural", () => {
    expect(pluralize(-3, "fact")).toBe("facts");
  });

  it("honors an explicit irregular plural", () => {
    expect(pluralize(1, "entry", "entries")).toBe("entry");
    expect(pluralize(3, "entry", "entries")).toBe("entries");
    expect(pluralize(0, "child", "children")).toBe("children");
  });
});

describe("formatCount", () => {
  it("renders '0' for nullish input", () => {
    expect(formatCount(undefined)).toBe("0");
    expect(formatCount(null)).toBe("0");
  });

  it("renders '0' for non-finite input", () => {
    expect(formatCount(NaN)).toBe("0");
    expect(formatCount(Infinity)).toBe("0");
    expect(formatCount(-Infinity)).toBe("0");
  });

  it("renders 0 and small counts without a suffix", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(999)).toBe("999");
  });

  it("compacts thousands to a K suffix", () => {
    expect(formatCount(1000)).toBe("1K");
    expect(formatCount(1500)).toBe("1.5K");
    expect(formatCount(1234)).toBe("1.2K");
  });

  it("compacts millions and billions", () => {
    expect(formatCount(2_000_000)).toBe("2M");
    expect(formatCount(1_000_000_000)).toBe("1B");
  });

  it("preserves the sign for negative counts", () => {
    expect(formatCount(-1500)).toBe("-1.5K");
  });
});

describe("formatRelativeTime", () => {
  it("returns the em-dash sentinel for nullish / empty input", () => {
    expect(formatRelativeTime(null)).toEqual({ text: EM_DASH, title: "" });
    expect(formatRelativeTime(undefined)).toEqual({ text: EM_DASH, title: "" });
    expect(formatRelativeTime("")).toEqual({ text: EM_DASH, title: "" });
  });

  it("returns the sentinel for unparseable strings", () => {
    expect(formatRelativeTime("not a date")).toEqual({
      text: EM_DASH,
      title: "",
    });
  });

  it("returns the sentinel for whitespace-only strings", () => {
    // "   " is not === "" so it flows into the parser, which yields null.
    expect(formatRelativeTime("   ")).toEqual({ text: EM_DASH, title: "" });
  });

  describe("with a pinned clock", () => {
    const NOW = new Date("2026-07-02T12:05:00Z");

    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(NOW);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("phrases a recent past ISO timestamp as 'N minutes ago' with a local tooltip", () => {
      const iso = "2026-07-02T12:00:00Z"; // 5 minutes before NOW
      const { text, title } = formatRelativeTime(iso);
      expect(text).toBe("5 minutes ago");
      expect(title).toBe(new Date(iso).toLocaleString());
    });

    it("phrases a future ISO timestamp with an 'in ...' suffix", () => {
      const iso = "2026-07-02T12:15:00Z"; // 10 minutes after NOW
      const { text } = formatRelativeTime(iso);
      expect(text).toBe("in 10 minutes");
    });

    it("parses an epoch-milliseconds string via the numeric fallback branch", () => {
      const iso = "1700000000000"; // 2023-11-14, well in the past
      const { text, title } = formatRelativeTime(iso);
      expect(text).not.toBe(EM_DASH);
      expect(text).toMatch(/ago$/);
      expect(title).toBe(new Date(1_700_000_000_000).toLocaleString());
    });
  });
});
