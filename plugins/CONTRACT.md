# ElephantBroker Plugin Contract

This document is the shared adapter contract for ElephantBroker plugins across
Claude Code, Antigravity CLI, OpenCode, OpenClaw, and Hermes Agent. Host-specific
plugin APIs differ; the ElephantBroker boundary must not.

## Scope

Plugins are thin HTTP adapters. They translate host lifecycle events, tools, and
identity into ElephantBroker runtime requests. They must not implement scoring,
policy, storage, retrieval intelligence, or ontology rules locally.

## Identity Headers

Every ElephantBroker HTTP request must use the same identity header rules:

| Header | Source | Required for writes | Rule |
| --- | --- | --- | --- |
| `X-EB-Gateway-ID` | `EB_GATEWAY_ID` or host config | Yes | Stable gateway identity. |
| `X-EB-Agent-Key` | `EB_AGENT_KEY` or host-derived agent key | Recommended | Stable agent authority key. |
| `X-EB-Agent-ID` | Host agent id when available | No | Human-readable agent id. |
| `X-EB-Actor-Id` | `EB_ACTOR_ID` or host actor context | For authority-gated admin writes | Registered actor UUID. |
| `X-EB-Auth-Token` | `EB_AUTH_TOKEN` | Deployment-dependent | Runtime auth token. |

Adapters must trim values before sending. Empty values must be omitted, never
sent as empty strings.

## Service URL Resolution

Adapters must resolve the runtime URL in this order:

1. `EB_SERVICE_URL`
2. `EB_RUNTIME_URL`
3. `COGNEE_SERVICE_URL` for backward-compatible deployments
4. `http://localhost:8420`

The resolved URL is trimmed and stripped of trailing slashes.

## Session Identity

Adapters receive host-specific session keys. ElephantBroker accepts both a
stable `session_key` and UUID-shaped `session_id`.

When a host provides only an arbitrary string, adapters must derive a stable UUID
from that string. The derivation contract is:

- valid UUID input is returned unchanged;
- empty input maps to `00000000-0000-0000-0000-000000000000`;
- arbitrary non-empty input maps deterministically via SHA-256 truncated to 128
  bits and formatted as a UUID.

## Fail-Closed Writes

Write operations must not create unattributed memory or lifecycle records. If
`X-EB-Gateway-ID` cannot be resolved, adapters must skip write requests and log
a structured warning.

Write operations include:

- `/memory/store`
- `/memory/ingest-turn`
- `/memory/ingest-messages`
- `/sessions/start`
- `/sessions/end`
- admin, goal, procedure, artifact, and guard mutation endpoints

Read-only operations may run without a gateway when the host surface requires
best-effort recall, but they must still omit empty identity headers.

## Lifecycle Capture

Automatic memory capture is part of the product contract. Plugins must preserve
the host's completed-turn capture path:

- Claude Code: `UserPromptSubmit`, `PostToolUse`, `PreCompact`, `SessionEnd`
- Antigravity CLI: `PreInvocation`, `PostToolUse`, `PostInvocation`, `Stop`
- OpenCode: session/message events plus custom memory tools
- OpenClaw memory: `before_agent_start`, `agent_end`, `session_start`, `session_end`
- OpenClaw context: `registerContextEngine` lifecycle plus context hooks
- Hermes Agent: `prefetch`, `sync_turn`, `on_session_end`, `on_pre_compress`

Writes triggered from lifecycle hooks must not block the host interaction path
for unbounded time. If the host supports async hooks, use them. If it does not,
queue work and flush on final lifecycle events.

## Logging

Adapters must log enough for operators to answer: what request was skipped,
which identity was missing, which session was affected, and whether data was
durably written.

Host-native structured logging is preferred when available. Otherwise adapters
must write bounded structured log records to their plugin data directory. Debug
logging must be opt-in.

## Manifest Shape

Each host plugin must expose host-native discovery metadata:

- Claude Code: `.claude-plugin/plugin.json` at plugin root; `hooks/`, `skills/`,
  and `agents/` at plugin root.
- Antigravity CLI: `plugin.json` plus root `hooks.json`; command paths must be
  stable from the plugin root.
- OpenCode: plugin metadata plus JS/TS plugin module exporting the plugin.
- OpenClaw: `openclaw.plugin.json` with runtime `entry`, `configSchema`, and
  `contracts.tools` for every registered tool.
- Hermes Agent: `plugin.yaml` with `provider_type: memory` and implemented hook
  names.

## Drift Control

All plugin changes that affect identity, URL resolution, lifecycle capture, or
manifest shape must update conformance tests under `plugins/_shared` or the
host-specific test suite. Backend API changes must be shipped with adapter test
updates in the same branch.
