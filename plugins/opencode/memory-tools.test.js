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

async function runTool(toolName, args, options = {}) {
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

  global.fetch = async (url, requestOptions) => {
    requests.push({ url, options: requestOptions, body: requestOptions.body ? JSON.parse(requestOptions.body) : undefined });
    if (options.fetch) return options.fetch(url, requestOptions, requests);
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        id: "11111111-1111-1111-1111-111111111111",
        text: "updated",
        category: "general",
        scope: "session",
        confidence: 1,
        memory_class: "episodic",
        created_at: "2026-07-06T00:00:00Z",
        updated_at: "2026-07-06T00:00:00Z",
        use_count: 0,
      }),
    };
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = {
      EB_RUNTIME_URL: "http://eb.local",
      EB_GATEWAY_ID: "gw-test",
      EB_AGENT_KEY: "agent-test",
      EB_ACTOR_ID: "22222222-2222-2222-2222-222222222222",
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

test("memory_search includes session identity for session scope", async () => {
  const { requests } = await runTool("memory_search", { query: "probe", scope: "session" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://eb.local/memory/search");
  assert.equal(requests[0].body.scope, "session");
  assert.equal(requests[0].body.session_key, "agent:main:main");
  assert.match(requests[0].body.session_id, /^[0-9a-f-]{36}$/);
  assert.equal(Object.hasOwn(requests[0].body, "profile_name"), false);
});

test("memory_search omits session identity when scope is omitted", async () => {
  const { requests } = await runTool("memory_search", { query: "probe" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.scope, undefined);
  assert.equal(Object.hasOwn(requests[0].body, "session_key"), false);
  assert.equal(Object.hasOwn(requests[0].body, "session_id"), false);
});

test("memory_search omits session identity for team scope", async () => {
  const { requests } = await runTool("memory_search", { query: "probe", scope: "team" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.scope, "team");
  assert.equal(Object.hasOwn(requests[0].body, "session_key"), false);
  assert.equal(Object.hasOwn(requests[0].body, "session_id"), false);
});

test("memory_search omits session identity for organization scope", async () => {
  const { requests } = await runTool("memory_search", { query: "probe", scope: "organization" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.scope, "organization");
  assert.equal(Object.hasOwn(requests[0].body, "session_key"), false);
  assert.equal(Object.hasOwn(requests[0].body, "session_id"), false);
});

test("memory_search omits session identity for global scope", async () => {
  const { requests } = await runTool("memory_search", { query: "probe", scope: "global" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.scope, "global");
  assert.equal(Object.hasOwn(requests[0].body, "session_key"), false);
  assert.equal(Object.hasOwn(requests[0].body, "session_id"), false);
});

test("memory_search_global sends global scope to ElephantBroker", async () => {
  const { requests } = await runTool("memory_search_global", { query: "probe" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://eb.local/memory/search");
  assert.equal(requests[0].body.scope, "global");
  assert.equal(Object.hasOwn(requests[0].body, "session_key"), false);
  assert.equal(Object.hasOwn(requests[0].body, "session_id"), false);
});

test("memory_store forwards explicit team scope to ElephantBroker", async () => {
  const { requests } = await runTool("memory_store", { text: "shared fact", scope: "team" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://eb.local/memory/store");
  assert.equal(requests[0].body.fact.scope, "team");
  assert.equal(requests[0].body.session_key, "agent:main:main");
  assert.match(requests[0].body.session_id, /^[0-9a-f-]{36}$/);
});

test("memory_store forwards explicit organization scope to ElephantBroker", async () => {
  const { requests } = await runTool("memory_store", { text: "shared fact", scope: "organization" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://eb.local/memory/store");
  assert.equal(requests[0].body.fact.scope, "organization");
  assert.equal(requests[0].body.session_key, "agent:main:main");
  assert.match(requests[0].body.session_id, /^[0-9a-f-]{36}$/);
});

test("memory_store reports backend fact id instead of gateway id", async () => {
  const factId = "11111111-1111-1111-1111-111111111111";
  const { output } = await runTool("memory_store", { text: "stored fact" }, {
    fetch: async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        id: factId,
        gateway_id: "gw-test",
        text: "stored fact",
        category: "general",
        scope: "session",
        confidence: 1,
        memory_class: "episodic",
        created_at: "2026-07-06T00:00:00Z",
        updated_at: "2026-07-06T00:00:00Z",
        use_count: 0,
      }),
    }),
  });

  assert.match(output, new RegExp(`id: ${factId}`));
  assert.doesNotMatch(output, /id: gw-test/);
});

test("memory_forget handles backend not-found response without losing client binding", async () => {
  const { output } = await runTool("memory_forget", { id: "11111111-1111-1111-1111-111111111111" }, {
    fetch: async () => ({
      ok: false,
      status: 404,
      text: async () => "",
    }),
  });

  assert.match(output, /Failed to delete memory/);
  assert.doesNotMatch(output, /undefined is not an object|_client/);
});

test("memory_forget treats empty successful delete response as deleted", async () => {
  const factId = "11111111-1111-1111-1111-111111111111";
  const { output } = await runTool("memory_forget", { id: factId }, {
    fetch: async () => ({
      ok: true,
      status: 204,
      text: async () => "",
    }),
  });

  assert.equal(output, `Memory ${factId} deleted.`);
});

test("memory_update omits backend-forbidden and blank optional fields", async () => {
  const { requests, output } = await runTool("memory_update", {
    id: "11111111-1111-1111-1111-111111111111",
    text: "updated",
    category: "",
    confidence: 0.99,
    decision_status: "actioned",
    entity_type: "Document",
    archived: false,
  });

  assert.match(output, /updated successfully/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://eb.local/memory/11111111-1111-1111-1111-111111111111");
  assert.deepEqual(requests[0].body, {
    text: "updated",
    confidence: 0.99,
    archived: false,
  });
});

test("memory_update rejects non-UUID goal_ids before HTTP", async () => {
  const { requests, output } = await runTool("memory_update", {
    id: "11111111-1111-1111-1111-111111111111",
    goal_ids: ["not-a-uuid"],
  });

  assert.equal(requests.length, 0);
  assert.match(output, /Invalid goal_id/);
});
