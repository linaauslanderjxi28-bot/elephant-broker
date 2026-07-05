const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

test("OpenCode metadata stays informational rather than load-bearing", () => {
  const metadata = JSON.parse(readFileSync(join(__dirname, "plugin.json"), "utf8"));

  assert.equal(metadata.name, "elephantbroker-memory");
  assert.equal(metadata.keywords.includes("opencode"), true);
  assert.equal(Object.hasOwn(metadata, "entry"), false);
  assert.equal(Object.hasOwn(metadata, "hooks"), false);
});
