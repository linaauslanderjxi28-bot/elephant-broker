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

async function loadPlugin(fetchHandler) {
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
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ url, options, body });
    return fetchHandler ? fetchHandler(url, options, body) : { ok: true, status: 200, text: async () => "{}" };
  };

  delete require.cache[require.resolve("./elephantbroker-memory.js")];
  process.env = {
    EB_RUNTIME_URL: "http://eb.test",
    EB_GATEWAY_ID: "gw-test",
    EB_AGENT_KEY: "agent-test",
  };

  const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
  const plugin = await ElephantBrokerMemory();

  return {
    plugin,
    requests,
    cleanup: () => {
      Module._load = originalLoad;
      global.fetch = originalFetch;
      process.env = originalEnv;
    },
  };
}

test("tool.execute.after stores tool output audit memory", async () => {
  const ctx = await loadPlugin();
  try {
    await ctx.plugin["tool.execute.after"](
      { tool: "bash", sessionID: "s1", callID: "call-1", args: { command: "date" } },
      { title: "date", output: "Tue Jul 07", metadata: { exit: 0 } },
    );

    const store = ctx.requests.find((request) => request.url === "http://eb.test/memory/store");
    assert.equal(store.body.fact.category, "tool-call");
    assert.match(store.body.fact.text, /Tool bash completed/);
    assert.match(store.body.fact.text, /Tue Jul 07/);
  } finally {
    ctx.cleanup();
  }
});

test("experimental.session.compacting flushes buffered chat messages", async () => {
  const ctx = await loadPlugin();
  try {
    await ctx.plugin["chat.message"](
      { sessionID: "s1", messageID: "m1" },
      { message: { id: "m1" }, parts: [{ type: "text", text: "remember before compact" }] },
    );
    const output = { context: [] };
    await ctx.plugin["experimental.session.compacting"]({ sessionID: "s1" }, output);

    const ingest = ctx.requests.find((request) => request.url === "http://eb.test/memory/ingest-turn");
    assert.equal(ingest.body.messages[0].content, "remember before compact");
    assert.match(output.context[0], /ElephantBroker flushed/);
  } finally {
    ctx.cleanup();
  }
});

test("experimental.chat.system.transform injects recalled memory context", async () => {
  const ctx = await loadPlugin((url, _options, body) => {
    if (url === "http://eb.test/memory/search") {
      assert.equal(body.scope, "session");
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify([
          { category: "preference", text: "User prefers concise summaries", confidence: 1, score: 0.91 },
        ]),
      };
    }
    return { ok: true, status: 200, text: async () => "{}" };
  });
  try {
    const output = { system: [] };
    await ctx.plugin["experimental.chat.system.transform"]({ sessionID: "s1", model: {} }, output);

    assert.match(output.system[0], /ElephantBroker recalled context/);
    assert.match(output.system[0], /User prefers concise summaries/);
  } finally {
    ctx.cleanup();
  }
});
