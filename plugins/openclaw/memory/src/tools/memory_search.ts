import type { ElephantBrokerClient } from "../client.js";

const AUDIT_CATEGORIES = new Set(["tool-call", "conversation", "todowrite"]);

function filterAuditResults<T extends { category: string }>(results: T[], includeAudit: boolean): T[] {
  if (includeAudit) return results;
  return results.filter((result) => !AUDIT_CATEGORIES.has(result.category));
}

export function createMemorySearchTool(client: ElephantBrokerClient) {
  return {
    id: "memory_search",
    name: "memory_search",
    description: "Search long-term memory for relevant facts, preferences, and context.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        max_results: { type: "number", description: "Maximum results to return" },
        scope: { type: "string", description: "Scope filter (global, session, actor)" },
        memory_class: { type: "string", description: "Memory class filter" },
        include_audit: { type: "boolean", description: "Include tool-call/conversation/todowrite audit records (default: false)" },
      },
      required: ["query"],
    },
    async execute(toolCallId: string, params: { query: string; max_results?: number; scope?: string; memory_class?: string; include_audit?: boolean }, signal?: AbortSignal) {
      const includeAudit = params.include_audit ?? false;
      const results = await client.search({
        query: params.query,
        max_results: params.max_results,
        scope: params.scope,
        memory_class: params.memory_class,
        include_audit: includeAudit,
      });
      const filtered = filterAuditResults(results, includeAudit);
      return {
        content: [{ type: "text", text: JSON.stringify({
          results: filtered.map((r) => ({
            fact_id: r.id,
            text: r.text,
            category: r.category,
            memory_class: r.memory_class,
            confidence: r.confidence,
            score: r.score,
            created_at: r.created_at,
          })),
          total: filtered.length,
        }) }],
      };
    },
  };
}
