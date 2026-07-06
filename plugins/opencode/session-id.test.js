const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const Module = require("node:module");
const test = require("node:test");

function schemaNode() {
  return {
    describe: () => schemaNode(),
    optional: () => schemaNode(),
    default: () => schemaNode(),
  };
}

function stableUuid(text) {
  const hex = crypto.createHash("sha256").update(String(text), "utf8").digest("hex").slice(0, 32);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

test("OpenCode derives arbitrary session IDs with SHA-256 truncated UUID contract", async () => {
  const originalLoad = Module._load;
  const originalFetch = global.fetch;
  const originalEnv = { ...process.env };
  const capturedBodies = [];

  Module._load = (request, parent, isMain) => {
    if (request === "@opencode-ai/plugin") {
      const tool = (definition) => definition;
      tool.schema = { string: schemaNode, number: schemaNode, boolean: schemaNode, array: schemaNode };
      return { tool };
    }
    return originalLoad(request, parent, isMain);
  };

  global.fetch = async (_url, options) => {
    if (options.body) capturedBodies.push(JSON.parse(options.body));
    return { ok: true, status: 200, text: async () => "{}" };
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = {
      EB_RUNTIME_URL: "http://runtime.test",
      EB_GATEWAY_ID: "gw-test",
    };

    const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
    const plugin = await ElephantBrokerMemory();
    await plugin.event({ event: { type: "session.created", properties: { sessionID: "session:key" } } });
    await plugin.tool.memory_store.execute({ text: "remember this", category: "general", scope: "session" });

    const storeBody = capturedBodies.find((body) => body.fact?.text === "remember this");
    assert.equal(storeBody.session_key, "opencode:session:key");
    assert.equal(storeBody.session_id, stableUuid("opencode:session:key:session:key"));
  } finally {
    Module._load = originalLoad;
    global.fetch = originalFetch;
    process.env = originalEnv;
  }
});
