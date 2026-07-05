import type { ElephantBrokerClient } from "../client.js";

export function createClaimGetTool(client: Pick<ElephantBrokerClient, "getClaim">) {
  return {
    id: "claim_get",
    name: "claim_get",
    description: "Read a claim and its current verification state by claim ID.",
    parameters: {
      type: "object",
      properties: {
        claim_id: { type: "string", description: "Claim UUID to read" },
      },
      required: ["claim_id"],
    },
    async execute(_toolCallId: string, params: { claim_id: string }, _signal?: AbortSignal) {
      const result = await client.getClaim(params.claim_id);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
      };
    },
  };
}
