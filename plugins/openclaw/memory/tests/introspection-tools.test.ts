import { describe, expect, it } from "vitest";
import { createActorInspectTool } from "../src/tools/actor_inspect.js";
import { createClaimGetTool } from "../src/tools/claim_get.js";
import { createProcedureAuditLookupTool } from "../src/tools/procedure_audit_lookup.js";

class RecordingClient {
  readonly calls: string[] = [];

  async inspectActor(actorId: string, options: { include_relationships?: boolean; include_authority_chain?: boolean }) {
    this.calls.push(`actor:${actorId}:${Boolean(options.include_relationships)}:${Boolean(options.include_authority_chain)}`);
    return { actor: { id: actorId }, relationships: [], authority_chain: [] };
  }

  async getClaim(claimId: string) {
    this.calls.push(`claim:${claimId}`);
    return { id: claimId, status: "pending" };
  }

  async lookupProcedureAudit(request: { action_id?: string; lineage_ref?: string }) {
    this.calls.push(`audit:${request.action_id ?? ""}:${request.lineage_ref ?? ""}`);
    return [{ id: "event-1" }];
  }
}

describe("read-only introspection tools", () => {
  it("inspects actor relationships and authority chain when requested", async () => {
    const client = new RecordingClient();
    const tool = createActorInspectTool(client);

    const result = await tool.execute("call-1", {
      actor_id: "actor-1",
      include_relationships: true,
      include_authority_chain: true,
    });

    expect(client.calls).toEqual(["actor:actor-1:true:true"]);
    expect(result.content[0].text).toContain("actor-1");
  });

  it("gets claim state without mutating verification status", async () => {
    const client = new RecordingClient();
    const tool = createClaimGetTool(client);

    const result = await tool.execute("call-2", { claim_id: "claim-1" });

    expect(client.calls).toEqual(["claim:claim-1"]);
    expect(result.content[0].text).toContain("pending");
  });

  it("looks up procedure audit events by action id", async () => {
    const client = new RecordingClient();
    const tool = createProcedureAuditLookupTool(client);

    const result = await tool.execute("call-3", { action_id: "action-1" });

    expect(client.calls).toEqual(["audit:action-1:"]);
    expect(result.content[0].text).toContain("event-1");
  });

  it("looks up procedure audit events by lineage ref", async () => {
    const client = new RecordingClient();
    const tool = createProcedureAuditLookupTool(client);

    await tool.execute("call-4", { lineage_ref: "lineage-1" });

    expect(client.calls).toEqual(["audit::lineage-1"]);
  });

  it("rejects ambiguous procedure audit lookup parameters before making a request", async () => {
    const client = new RecordingClient();
    const tool = createProcedureAuditLookupTool(client);

    await expect(tool.execute("call-5", { action_id: "action-1", lineage_ref: "lineage-1" })).rejects.toThrow(
      "Provide exactly one of action_id or lineage_ref",
    );
    expect(client.calls).toEqual([]);
  });
});
