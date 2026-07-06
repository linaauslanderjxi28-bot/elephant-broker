# ElephantBroker Plugins

This directory contains the agent-facing ElephantBroker plugins that call the
backend runtime over HTTP with `X-EB-*` identity headers.

`elephant-broker` is now the monorepo source of truth for both backend and
plugins. The old standalone `elephantbroker-memory-plugins` repository is a
migration source/archive; new plugin changes should happen here.

## Directory Structure

- `claude-code/` — Claude Code plugin with hooks, skills, and agents.
- `antigravity-cli/` — Antigravity CLI (`agy`) plugin.
- `opencode/` — OpenCode plugin.
- `openclaw/memory/` — OpenClaw native memory plugin.
- `openclaw/context/` — OpenClaw context-engine plugin.
- `openclaw/shared/` — Shared OpenClaw plugin helpers. This is temporary; if the
  OpenClaw plugins are published independently, extract it into a real package.
- `hermes-agent/` — Hermes Agent memory provider plugin.
- `configs/` — Runtime config snapshots/templates for each agent.

## Host Install Paths

- Claude Code: `~/.claude/plugins/elephantbroker-memory/`
- Antigravity CLI: `~/.gemini/antigravity-cli/plugins/elephantbroker-memory/`
- OpenCode: `~/.config/opencode/plugins/elephantbroker-memory.ts`
- OpenClaw: `~/.openclaw/extensions/elephantbroker-memory/` and
  `~/.openclaw/extensions/elephantbroker-context/`
- Hermes Agent: `~/.hermes/plugins/elephantbroker/`

## Ownership Rules

- Backend APIs, schemas, scoring, policy, retrieval, and storage live outside
  this directory under `elephantbroker/` and related backend folders.
- Plugin adapters live here. They should stay thin: translate agent/plugin
  contracts to ElephantBroker HTTP calls and forward identity/auth headers.
- When a backend API changes, update the matching plugin adapter in the same
  branch and run both backend and plugin tests.

## OpenClaw Layout

OpenClaw uses two packages:

- `openclaw/memory/` provides memory/search/goal/procedure/guard/artifact/admin
  tools and session lifecycle hooks.
- `openclaw/context/` provides the context engine lifecycle: bootstrap, ingest,
  assemble, compact, after-turn, and subagent events.

Build and test from each package directory:

```bash
cd plugins/openclaw/memory && npm ci && npm test && npm run build
cd plugins/openclaw/context && npm ci && npm test && npm run build
```
