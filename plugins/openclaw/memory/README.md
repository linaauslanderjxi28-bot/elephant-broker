## OpenClaw ElephantBroker Plugin

This package contains the OpenClaw ElephantBroker memory plugin. The runtime
entrypoint is `index.ts`, which re-exports `src/index.ts`; package metadata and
`openclaw.plugin.json` point OpenClaw at the built `dist/index.js` artifact.

This package is the source of truth for the OpenClaw memory plugin inside the
ElephantBroker monorepo.

OpenClaw plugins use the OpenClaw plugin contract and `api.registerTool(...)`.
The OpenCode plugin implementation lives in `../../opencode/` and should not
be mirrored here, because it imports the OpenCode SDK instead of OpenClaw's
plugin API.

Set `EB_ACTOR_ID` to a registered authority actor UUID when using memory write,
update, delete, or admin tools against a runtime-hardening EB backend. The
OpenClaw client forwards it as `X-EB-Actor-Id`.

The plugin manifest declares startup activation, compatibility bounds, and the
full `contracts.tools` list so OpenClaw can discover tool ownership without
eagerly loading the runtime bundle.

The old `openclaw-plugins/elephantbroker-memory/` copy has been merged here.
Future OpenClaw memory-plugin changes belong in this package.

## Troubleshooting: Tools not showing up in OpenClaw

If you have configured `tools.profile: "full"` in `openclaw.json` but the agent claims it does not have tools like `memory_store` or `memory_search`:

**Root Cause:**
OpenClaw enforces a `sandbox` tool policy filter before passing tools to the LLM. If `tools.alsoAllow` or `tools.allow` is omitted in your `openclaw.json`, it falls back to a restrictive hardcoded list (`DEFAULT_TOOL_ALLOW` in OpenClaw core), which only includes basic system tools like `exec`, `read`, `write`, `process`. As a result, all plugin-registered tools are quietly intercepted and removed by the sandbox policy, even if the profile is set to `full`. (Check `journalctl --user -u openclaw-gateway` for logs like `tool policy removed ... via sandbox tools.allow`).

**Solution:**
You must explicitly configure a wildcard pass (or list the specific tools) under the `"tools"` block in `~/.openclaw/openclaw.json`:
```json
  "tools": {
    "profile": "full",
    "alsoAllow": ["*"],
    "elevated": {
      "enabled": true
    }
  }
```
This instructs the OpenClaw tool policy sandbox to stop filtering out unregistered custom tools.
