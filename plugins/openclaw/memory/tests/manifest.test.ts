import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const manifest = JSON.parse(
  readFileSync(resolve(__dirname, "../openclaw.plugin.json"), "utf8"),
);
const packageJson = JSON.parse(
  readFileSync(resolve(__dirname, "../package.json"), "utf8"),
);

describe("openclaw.plugin.json", () => {
  it("declares OpenClaw discovery metadata for every registered tool", () => {
    expect(manifest.kind).toBe("memory");
    expect(manifest.activation).toEqual({ onStartup: true });
    expect(manifest.compat.pluginApi).toBe(">=2026.3.24-beta.2");
    expect(manifest.compat.minGatewayVersion).toBe("2026.3.24-beta.2");
    expect(manifest.contracts.tools).toEqual([
      "memory_search",
      "memory_search_global",
      "memory_get",
      "memory_store",
      "memory_forget",
      "memory_update",
      "session_goals_list",
      "goal_create",
      "session_goals_update_status",
      "session_goals_add_blocker",
      "session_goals_progress",
      "procedure_create",
      "procedure_activate",
      "procedure_complete",
      "procedure_complete_step",
      "procedure_session_status",
      "procedure_status",
      "procedure_audit_lookup",
      "actor_inspect",
      "claim_get",
      "guards_list",
      "guard_status",
      "artifact_search",
      "create_artifact",
      "admin_create_org",
      "admin_create_team",
      "admin_register_actor",
      "admin_add_member",
      "admin_remove_member",
      "admin_merge_actors",
    ]);
  });

  it("exposes actorId as configurable authority identity", () => {
    expect(manifest.configSchema.properties.actorId).toEqual({
      type: "string",
      description: "Registered EB actor UUID for authority-gated writes",
    });
  });

  it("keeps package entrypoint and OpenClaw SDK metadata aligned", () => {
    expect(packageJson.main).toBe(manifest.entry);
    expect(packageJson.openclaw.extensions).toEqual([manifest.entry]);
    expect(packageJson.openclaw.compat).toEqual(manifest.compat);
    expect(packageJson.openclaw.build.openclawVersion).toBe("2026.3.24-beta.2");
    expect(packageJson.openclaw.build.pluginSdkVersion).toBe("2026.3.24-beta.2");
  });
});
