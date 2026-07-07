import { describe, expect, it, vi } from "vitest";
import { createMemorySearchTool } from "../src/tools/memory_search.js";

function parseResult(result: { content: Array<{ type: string; text: string }> }) {
  return JSON.parse(result.content[0].text);
}

describe("memory_search audit filtering", () => {
  it("filters audit results by default and sends include_audit=false", async () => {
    const audit = { id: "audit", text: "audit log", category: "tool-call", memory_class: "episodic", confidence: 1, score: 1, created_at: "2026-07-07T00:00:00Z" };
    const fact = { id: "fact", text: "business fact", category: "general", memory_class: "episodic", confidence: 1, score: 1, created_at: "2026-07-07T00:00:00Z" };
    const client = { search: vi.fn().mockResolvedValue([audit, fact]) };
    const tool = createMemorySearchTool(client as unknown as Parameters<typeof createMemorySearchTool>[0]);

    const result = await tool.execute("tc1", { query: "probe" });

    expect(client.search).toHaveBeenCalledWith({ query: "probe", max_results: undefined, scope: undefined, memory_class: undefined, include_audit: false });
    expect(parseResult(result).results).toEqual([{ fact_id: "fact", text: "business fact", category: "general", memory_class: "episodic", confidence: 1, score: 1, created_at: "2026-07-07T00:00:00Z" }]);
  });

  it("keeps audit results when include_audit=true", async () => {
    const audit = { id: "audit", text: "audit log", category: "tool-call", memory_class: "episodic", confidence: 1, score: 1, created_at: "2026-07-07T00:00:00Z" };
    const fact = { id: "fact", text: "business fact", category: "general", memory_class: "episodic", confidence: 1, score: 1, created_at: "2026-07-07T00:00:00Z" };
    const client = { search: vi.fn().mockResolvedValue([audit, fact]) };
    const tool = createMemorySearchTool(client as unknown as Parameters<typeof createMemorySearchTool>[0]);

    const result = await tool.execute("tc2", { query: "probe", include_audit: true });

    expect(client.search).toHaveBeenCalledWith({ query: "probe", max_results: undefined, scope: undefined, memory_class: undefined, include_audit: true });
    expect(parseResult(result).total).toBe(2);
  });
});
