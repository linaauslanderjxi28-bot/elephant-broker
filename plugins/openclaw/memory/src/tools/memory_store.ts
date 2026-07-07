import type { ElephantBrokerClient } from "../client.js";

export function createMemoryStoreTool(client: ElephantBrokerClient) {
  return {
    id: "memory_store",
    name: "memory_store",
    description: "Store a new fact in long-term memory.",
    parameters: {
      type: "object",
      properties: {
        text: { type: "string", description: "The fact text to store" },
        category: { type: "string", description: "Category (preference, decision, event, etc.)" },
        scope: { type: "string", description: "Visibility scope" },
        confidence: { type: "number", description: "Confidence 0.0-1.0" },
        memory_class: { type: "string", description: "Memory class (episodic, semantic, procedural, policy, working_memory)" },
        entity_type: { type: "string", description: "Optional entity type, e.g. Document or ResearchDecision" },
        entity_name: { type: "string", description: "Optional entity display name for typed facts" },
        goal_ids: { type: "array", items: { type: "string" }, description: "Related goal UUIDs. Values must be UUID strings." },
        decision_status: { type: "string", description: "Decision status: proposed, approved, rejected, actioned" },
        decision_domain: { type: "string", description: "Optional decision domain tag" },
      },
      required: ["text"],
    },
    async execute(toolCallId: string, params: { text: string; category?: string; scope?: string; confidence?: number; memory_class?: string; entity_type?: string; entity_name?: string; goal_ids?: string[]; decision_status?: string; decision_domain?: string }, signal?: AbortSignal) {
      const sid = client.getSessionId();
      const profile = client.getProfileName();
      const result = await client.store({
        fact: {
          text: params.text,
          category: params.category || "general",
          ...(params.scope ? { scope: params.scope } : {}),
          ...(params.confidence !== undefined ? { confidence: params.confidence } : {}),
          ...(params.memory_class ? { memory_class: params.memory_class } : {}),
          ...(params.entity_type ? { entity_type: params.entity_type } : {}),
          ...(params.entity_name ? { entity_name: params.entity_name } : {}),
          ...(params.goal_ids?.length ? { goal_ids: params.goal_ids } : {}),
          ...(params.decision_status ? { decision_status: params.decision_status } : {}),
          ...(params.decision_domain ? { decision_domain: params.decision_domain } : {}),
        },
        session_key: client.getSessionKey(),
        ...(sid ? { session_id: sid } : {}),
        ...(profile ? { profile_name: profile } : {}),
      });
      if (result === null) {
        return {
          content: [{ type: "text", text: "Fact not stored: near-duplicate already exists in memory." }],
        };
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
      };
    },
  };
}
