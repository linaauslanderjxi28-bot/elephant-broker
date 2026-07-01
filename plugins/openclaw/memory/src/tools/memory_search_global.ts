import type { ElephantBrokerClient } from "../client.js";

export function createMemorySearchGlobalTool(client: ElephantBrokerClient) {
  return {
    id: "memory_search_global",
    name: "memory_search_global",
    description: "Search the global ElephantBroker knowledge base (team-shared memory). Use this for cross-session knowledge, imported documents, team context, and data from non-session pipelines like doc-ingestor or scrapling.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "The global search query (natural language)" },
        max_results: { type: "number", description: "Max global results to return (default: 20, max: 30)" },
        session_key: { type: "string", description: "Optional global session key filter, e.g. scrapling:example-com or doc-ingestor:0-inbox" },
        category: { type: "string", description: "Optional category filter, e.g. 'project' for doc-ingestor, 'finance' for financial data" },
        memory_class: { type: "string", description: "Optional memory class filter (semantic, episodic, procedural, policy)" },
      },
      required: ["query"],
    },
    async execute(toolCallId: string, params: { query: string; max_results?: number; session_key?: string; category?: string; memory_class?: string }, signal?: AbortSignal) {
      const results = await client.searchGlobal(params.query, {
        max_results: Math.min(params.max_results ?? 20, 30),
        session_key: params.session_key,
        memory_class: params.memory_class,
      });
      if (!results || results.length === 0) {
        return {
          content: [{ type: "text", text: JSON.stringify({ result: "No matching global memories found." }) }],
        };
      }
      const filtered = params.category
        ? results.filter((r) => r.category === params.category)
        : results;
      if (filtered.length === 0) {
        return {
          content: [{ type: "text", text: JSON.stringify({ result: `No global memories found matching category '${params.category}'.` }) }],
        };
      }
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