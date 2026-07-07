import { describe, it, expect, vi } from "vitest";

// Mock fetch and crypto
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("Plugin registration", () => {
  it("registers non-admin tools by default", async () => {
    const { register } = await import("../src/index.js");
    const tools: Array<{ id: string }> = [];
    const hooks: Record<string, unknown> = {};
    const api = {
      registerTool: (t: { id: string }) => tools.push(t),
      on: (event: string, handler: unknown) => { hooks[event] = handler; },
    };
    register(api);
    expect(tools.length).toBe(22);
    expect(tools.map(t => t.id)).not.toContain("admin_create_org");
  });

  it("registers admin tools when explicitly enabled", async () => {
    const { register } = await import("../src/index.js");
    const tools: Array<{ id: string }> = [];
    const api = {
      pluginConfig: { enableAdminTools: true },
      registerTool: (t: { id: string }) => tools.push(t),
      on: () => {},
    };
    register(api);
    expect(tools.length).toBe(28);
    expect(tools.map(t => t.id)).toContain("admin_create_org");
  });

  it("registers 4 hooks via api.on()", async () => {
    const { register } = await import("../src/index.js");
    const hooks: string[] = [];
    const api = {
      registerTool: () => {},
      on: (event: string) => hooks.push(event),
    };
    register(api);
    expect(hooks).toContain("before_agent_start");
    expect(hooks).toContain("agent_end");
    expect(hooks).toContain("session_start");
    expect(hooks).toContain("session_end");
    expect(hooks.length).toBe(4);
  });

  it("tool names match spec", async () => {
    const { register } = await import("../src/index.js");
    const tools: Array<{ id: string }> = [];
    const api = {
      registerTool: (t: { id: string }) => tools.push(t),
      on: () => {},
    };
    register(api);
    const ids = tools.map(t => t.id).sort();
    expect(ids).toEqual([
      "actor_inspect",
      "artifact_search",
      "claim_get",
      "create_artifact",
      "goal_create",
      "guard_status",
      "guards_list",
      "memory_forget",
      "memory_get",
      "memory_search",
      "memory_search_global",
      "memory_store",
      "memory_update",
      "procedure_activate",
      "procedure_audit_lookup",
      "procedure_complete_step",
      "procedure_create",
      "procedure_session_status",
      "session_goals_add_blocker",
      "session_goals_list",
      "session_goals_progress",
      "session_goals_update_status",
    ]);
  });

  it("memory_store describes goal_ids as goal UUIDs", async () => {
    const { register } = await import("../src/index.js");
    const tools: Array<{ id: string; parameters: { properties: Record<string, { description?: string }> } }> = [];
    const api = {
      registerTool: (t: { id: string; parameters: { properties: Record<string, { description?: string }> } }) => tools.push(t),
      on: () => {},
    };
    register(api);
    const memoryStore = tools.find(t => t.id === "memory_store");
    expect(memoryStore?.parameters.properties.goal_ids.description).toContain("goal UUIDs");
  });
});
