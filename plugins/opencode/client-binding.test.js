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

test("plugin logging preserves the OpenCode app client receiver", async () => {
  const originalLoad = Module._load;
  const originalEnv = { ...process.env };
  let loggedMessage = "";

  Module._load = (request, parent, isMain) => {
    if (request === "@opencode-ai/plugin") {
      const tool = (definition) => definition;
      tool.schema = { string: schemaNode, number: schemaNode, boolean: schemaNode, array: schemaNode };
      return { tool };
    }
    return originalLoad(request, parent, isMain);
  };

  try {
    delete require.cache[require.resolve("./elephantbroker-memory.js")];
    process.env = { EB_GATEWAY_ID: "" };
    const { ElephantBrokerMemory } = require("./elephantbroker-memory.js");
    const app = {
      _client: { connected: true },
      log(input) {
        if (!this._client.connected) throw new Error("client unavailable");
        loggedMessage = input.body.message;
        return Promise.resolve();
      },
    };

    await ElephantBrokerMemory({ client: { app } });
    assert.equal(loggedMessage, "EB_GATEWAY_ID not set; plugin loaded but inactive");
  } finally {
    Module._load = originalLoad;
    process.env = originalEnv;
  }
});
