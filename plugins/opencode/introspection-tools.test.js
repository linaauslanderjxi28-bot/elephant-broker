const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");

function schemaNode() {
  return {
    describe: () => schemaNode(),
    optional: () => schemaNode(),
    default: () => schemaNode(),
  };
}

async function runTool(toolName, args) {
  const originalLoad = Module._load;
  const originalFetch = global.fetch;
  const originalEnv = { ...process.env };
  const requests = [];

  Module._load = (request, parent, isMain) => {
    if (request === "@opencode-ai/plugin") {
      const tool = (definition) => definition;
      tool.schema = { string: schemaNode, number: schemaNode, boolean: schemaNode, array: schemaNode };
      return { tool };
    }
    return originalLoad(request, parent, isMain);
  };

  global.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, url }) };
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = {
      EB_RUNTIME_URL: "http://eb.test",
      EB_GATEWAY_ID: "gw-test",
      EB_AGENT_KEY: "agent-test",
      EB_ACTOR_ID: "actor-header",
    };
    const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
    const plugin = await ElephantBrokerMemory();
    const output = await plugin.tool[toolName].execute(args);
    return { output, requests };
  } finally {
    Module._load = originalLoad;
    global.fetch = originalFetch;
    process.env = originalEnv;
  }
}

test("actor_inspect uses read-only actor endpoints with EB headers", async () => {
  const { requests } = await runTool("actor_inspect", {
    actor_id: "actor-1",
    include_relationships: true,
    include_authority_chain: true,
  });

  assert.deepEqual(requests.map((request) => request.url), [
    "http://eb.test/actors/actor-1",
    "http://eb.test/actors/actor-1/relationships",
    "http://eb.test/actors/actor-1/authority-chain",
  ]);
  assert.equal(requests[0].options.method, "GET");
  assert.equal(requests[0].options.headers["X-EB-Gateway-ID"], "gw-test");
  assert.equal(requests[0].options.headers["X-EB-Session-Key"], "agent:main:main");
  assert.equal(requests[0].options.headers["X-EB-Agent-Key"], "agent-test");
  assert.equal(requests[0].options.headers["X-EB-Actor-Id"], "actor-header");
});

test("claim_get uses only the read-only claim endpoint", async () => {
  const { requests } = await runTool("claim_get", { claim_id: "claim-1" });

  assert.deepEqual(requests.map((request) => request.url), ["http://eb.test/claims/claim-1"]);
  assert.equal(requests[0].options.method, "GET");
});

test("procedure_audit_lookup uses action audit endpoint", async () => {
  const { requests } = await runTool("procedure_audit_lookup", { action_id: "action-1" });

  assert.deepEqual(requests.map((request) => request.url), ["http://eb.test/procedures/audit/action/action-1"]);
  assert.equal(requests[0].options.method, "GET");
});

test("procedure_audit_lookup uses lineage audit endpoint", async () => {
  const { requests } = await runTool("procedure_audit_lookup", { lineage_ref: "lineage 1" });

  assert.deepEqual(requests.map((request) => request.url), [
    "http://eb.test/procedures/audit/lineage?lineage_ref=lineage+1",
  ]);
  assert.equal(requests[0].options.method, "GET");
});

test("procedure_audit_lookup rejects ambiguous inputs before HTTP", async () => {
  const { output, requests } = await runTool("procedure_audit_lookup", {
    action_id: "action-1",
    lineage_ref: "lineage-1",
  });

  assert.equal(requests.length, 0);
  assert.match(output, /Provide exactly one of action_id or lineage_ref/);
});
