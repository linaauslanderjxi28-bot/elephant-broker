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
