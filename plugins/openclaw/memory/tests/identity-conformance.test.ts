import { describe, expect, it } from "vitest";

describe("ElephantBroker identity contract", () => {
  it("fails closed when EB_GATEWAY_ID is missing", async () => {
    const previous = process.env.EB_GATEWAY_ID;
    delete process.env.EB_GATEWAY_ID;
    const { ElephantBrokerClient } = await import("../src/client.js");

    expect(() => new ElephantBrokerClient("http://runtime.test")).toThrow(/EB_GATEWAY_ID is required/);

    if (previous === undefined) {
      delete process.env.EB_GATEWAY_ID;
    } else {
      process.env.EB_GATEWAY_ID = previous;
    }
  });

  it("manifest declares the gateway identity config field", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const manifest = JSON.parse(readFileSync(resolve(__dirname, "../openclaw.plugin.json"), "utf8"));

    expect(manifest.configSchema.properties.gatewayId).toEqual({
      type: "string",
      description: "Gateway instance ID",
    });
  });
});
