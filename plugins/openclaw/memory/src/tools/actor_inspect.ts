import type { ElephantBrokerClient } from "../client.js";

export function createActorInspectTool(client: Pick<ElephantBrokerClient, "inspectActor">) {
  return {
    id: "actor_inspect",
    name: "actor_inspect",
    description: "Read actor details with optional relationships and authority-chain context.",
    parameters: {
      type: "object",
      properties: {
        actor_id: { type: "string", description: "Actor UUID to inspect" },
        include_relationships: { type: "boolean", description: "Include actor relationship records" },
        include_authority_chain: { type: "boolean", description: "Include actor authority-chain records" },
      },
      required: ["actor_id"],
    },
    async execute(_toolCallId: string, params: {
      actor_id: string;
      include_relationships?: boolean;
      include_authority_chain?: boolean;
    }, _signal?: AbortSignal) {
      const result = await client.inspectActor(params.actor_id, {
        include_relationships: params.include_relationships,
        include_authority_chain: params.include_authority_chain,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
      };
    },
  };
}
