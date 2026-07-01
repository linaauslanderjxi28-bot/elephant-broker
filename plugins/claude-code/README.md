# ElephantBroker Memory Plugin for Claude Code

Adds persistent memory to Claude Code through ElephantBroker (EB) backend.

The integration:
- captures prompts, tool traces, and assistant responses into session memory
- injects relevant context on prompt submit
- flushes staged session memory through the EB backend on session end

## Runtime mode

The plugin runs exclusively in `eb` mode. Activation requires:
- `EB_MODE=true` environment variable, or
- `COGNEE_SERVICE_URL` pointing at the ElephantBroker service (port `8420`)

## Config

Set config in `<plugin-data-dir>/config.json`:

```json
{
  "service_url": "http://localhost:8420",
  "dataset": "claude_sessions",
  "agent_name": "elephantbroker-memory"
}
```

The data directory defaults to `~/.elephantbroker` and can be overridden via
the `CLAUDE_PLUGIN_DATA` environment variable.

## Enable plugin

```bash
claude --plugin-dir /path/to/elephantbroker-memory
```

## Hooks

| Hook | Behavior |
|---|---|
| `SessionStart` | EB session bootstrap, identity/agent setup, dataset readiness, watcher start |
| `UserPromptSubmit` | EB memory context lookup + async prompt staging |
| `PostToolUse` | Async trace write (with periodic memory context injection) |
| `Stop` | Assistant answer write + optional transcript clear |
| `PreCompact` | Memory anchor build before compaction (JSON output with `additionalContext`) |
| `SessionEnd` | Detached final sync worker |

## Skills

- `/elephantbroker-memory:elephantbroker-remember`
- `/elephantbroker-memory:elephantbroker-search`
- `/elephantbroker-memory:elephantbroker-sync`

## Status line (optional)

Configure in settings:
```json
{
  "statusLine": {
    "type": "command",
    "command": "<plugin-root>/scripts/eb-statusline.sh"
  }
}
```

## Files used

Files in `<plugin-data-dir>/` (default `~/.elephantbroker/`):
- `config.json` — plugin configuration
- `hook.log` — forensic debug log
- `counter.json` — per-session turn counter
- `save_counter.json` — save-kind counts per turn
- `pending_prompts.json` — staged prompts awaiting assistant answers
- `last_recall.json` — last search results metadata (for status line)
- `activity.ts` — last-activity timestamp (for idle watcher)
- `sync.lock` — cross-hook lock for sync work
- `watcher.pid` / `watcher.stop` — idle-watcher lifecycle
- `exit-watchers/` — per-session exit-watcher pidfiles
- `final-sync-once/` — one-shot markers (TTL 1 hour)

## EB endpoints used

- `POST /memory/search` — recall context
- `POST /memory/store` — direct fact store (fast path, fallback to ingest)
- `POST /memory/ingest-turn` — full turn ingest pipeline
- `POST /sessions/start` — session lifecycle
- `POST /sessions/end` — session lifecycle

## Troubleshooting

1. **Search issues**: Check `COGNEE_SERVICE_URL` and `EB_MODE`.
2. **Missing session key**: SessionStart logs `session_key_resolved` / `missing_payload_session_id`.
3. **Final sync**: Check `<plugin-data-dir>/hook.log` and `<plugin-data-dir>/exit-watcher.log`.

## Configuration reference

| Key | Env var(s) | Default | Notes |
|---|---|---|---|
| `dataset` | `COGNEE_PLUGIN_DATASET` | `claude_sessions` | Dataset name |
| `agent_name` | `COGNEE_AGENT_NAME` | `claude-code-agent` | Base name |
| `session_strategy` | `COGNEE_SESSION_STRATEGY` | `per-directory` | `per-directory`, `git-branch`, `static` |
| `service_url` | `COGNEE_SERVICE_URL` | `http://localhost:8420` | EB backend URL |
