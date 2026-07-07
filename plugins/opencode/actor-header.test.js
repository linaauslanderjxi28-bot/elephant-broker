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

async function runStoreWithHeaders(env) {
  const originalLoad = Module._load;
  const originalFetch = global.fetch;
  const originalEnv = { ...process.env };
  let capturedHeaders = {};

  Module._load = (request, parent, isMain) => {
    if (request === "@opencode-ai/plugin") {
      const tool = (definition) => definition;
      tool.schema = { string: schemaNode, number: schemaNode, boolean: schemaNode, array: schemaNode };
      return { tool };
    }
    return originalLoad(request, parent, isMain);
  };

  global.fetch = async (_url, options) => {
    capturedHeaders = options.headers;
    return { ok: true, status: 200, text: async () => "{}" };
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = {
      EB_RUNTIME_URL: "http://runtime.test",
      EB_GATEWAY_ID: "gw-test",
      EB_AGENT_KEY: "agent-test",
      ...env,
    };
    const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
    const plugin = await ElephantBrokerMemory();
    await plugin.tool.memory_store.execute({ text: "remember this", category: "general", scope: "session" });
    return capturedHeaders;
  } finally {
    Module._load = originalLoad;
    global.fetch = originalFetch;
    process.env = originalEnv;
  }
}

async function runStoreWithoutGateway() {
  const originalLoad = Module._load;
  const originalFetch = global.fetch;
  const originalEnv = { ...process.env };
  let fetchCalled = false;

  Module._load = (request, parent, isMain) => {
    if (request === "@opencode-ai/plugin") {
      const tool = (definition) => definition;
      tool.schema = { string: schemaNode, number: schemaNode, boolean: schemaNode, array: schemaNode };
      return { tool };
    }
    return originalLoad(request, parent, isMain);
  };

  global.fetch = async () => {
    fetchCalled = true;
    throw new Error("fetch should not run without EB_GATEWAY_ID");
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = { EB_RUNTIME_URL: "http://runtime.test" };
    const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
    const plugin = await ElephantBrokerMemory();
    const result = await plugin.tool.memory_store.execute({ text: "remember this", category: "general", scope: "session" });
    return { fetchCalled, result };
  } finally {
    Module._load = originalLoad;
    global.fetch = originalFetch;
    process.env = originalEnv;
  }
}

async function runStoreWithActor(actorId) {
  return runStoreWithHeaders({ EB_ACTOR_ID: actorId });
}

test("OpenCode memory writes include X-EB-Actor-Id when EB_ACTOR_ID is set", async () => {
  const headers = await runStoreWithActor("actor-123");
  assert.equal(headers["X-EB-Actor-Id"], "actor-123");
});

test("OpenCode memory writes omit X-EB-Actor-Id when EB_ACTOR_ID is blank", async () => {
  const headers = await runStoreWithActor("");
  assert.equal(Object.hasOwn(headers, "X-EB-Actor-Id"), false);
});

test("OpenCode memory writes include X-EB-Auth-Token when EB_AUTH_TOKEN is set", async () => {
  const headers = await runStoreWithHeaders({ EB_AUTH_TOKEN: " token-test " });
  assert.equal(headers["X-EB-Auth-Token"], "token-test");
  assert.equal(headers.Authorization, "Bearer token-test");
});

test("OpenCode memory writes include X-EB-Agent-ID when EB_AGENT_NAME is set", async () => {
  const headers = await runStoreWithHeaders({ EB_AGENT_NAME: "opencode-agent" });
  assert.equal(headers["X-EB-Agent-ID"], "opencode-agent");
});

test("OpenCode memory writes omit X-EB-Auth-Token when EB_AUTH_TOKEN is blank", async () => {
  const headers = await runStoreWithHeaders({ EB_AUTH_TOKEN: "  " });
  assert.equal(Object.hasOwn(headers, "X-EB-Auth-Token"), false);
  assert.equal(Object.hasOwn(headers, "Authorization"), false);
});

test("OpenCode memory writes fail closed when EB_GATEWAY_ID is missing", async () => {
  const { fetchCalled, result } = await runStoreWithoutGateway();
  assert.equal(fetchCalled, false);
  assert.match(result, /EB_GATEWAY_ID not configured/);
});
