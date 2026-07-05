import type { ElephantBrokerClient } from "../client.js";

export function createProcedureAuditLookupTool(client: Pick<ElephantBrokerClient, "lookupProcedureAudit">) {
  return {
    id: "procedure_audit_lookup",
    name: "procedure_audit_lookup",
    description: "Read procedure audit events by action ID or lineage reference.",
    parameters: {
      type: "object",
      properties: {
        action_id: { type: "string", description: "Action ID to look up" },
        lineage_ref: { type: "string", description: "Lineage reference to look up" },
      },
    },
    async execute(_toolCallId: string, params: { action_id?: string; lineage_ref?: string }, _signal?: AbortSignal) {
      if (Boolean(params.action_id) === Boolean(params.lineage_ref)) {
        throw new Error("Provide exactly one of action_id or lineage_ref");
      }
      const result = await client.lookupProcedureAudit(params);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
      };
    },
  };
}
