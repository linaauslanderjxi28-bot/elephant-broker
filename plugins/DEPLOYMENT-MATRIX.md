# Five-Agent ElephantBroker Deployment Matrix

## Overview

| Item | Value |
|------|-------|
| EB Runtime URL | `http://localhost:8420` |
| Gateway ID | `gw-enterprise-prod` |
| Shared Dataset | `elephantbroker` |
| HITL Middleware | `http://localhost:8421` |
| Embedding (vLLM) | `http://localhost:8001/v1` (model: `/model`, 1024d) |
| LLM (OneAPI) | `http://192.168.199.11:3001/v1` (model: `gemini-3.1-flash-lite`) |

## Agent Identity Matrix

| Agent | agent_name / agent_id | agent_key | Config Source | Launcher |
|-------|----------------------|-----------|---------------|----------|
| **Claude Code** | `openclaw-agent` | `openclaw-agent` | `~/.claude/settings.json` (`COGNEE_AGENT_NAME`) | `claude` (binary in PATH) |
| **AGY CLI** | `agy-cli-agent` | `agy-cli-agent` | `~/.elephantbroker/config.json` (`agent_name`) | `agy` (binary in PATH) |
| **Hermes** | `hermes-agent` | `hermes-agent` | `~/.hermes/elephantbroker.json` (`agent_key`) | `hermes` (binary in PATH) |
| **OpenCode** | `opencode-agent` | `opencode-agent` | `~/.local/bin/opencode` (wrapper exports `EB_AGENT_NAME`, `EB_AGENT_KEY`) | `opencode` (wrapper at `~/.local/bin/`) |
| **OpenClaw** | `openclaw-agent` | `openclaw-agent` | `~/.local/bin/openclaw` (wrapper exports `EB_AGENT_NAME`, `EB_AGENT_KEY`) | `openclaw` (wrapper at `~/.local/bin/`) |

## Plugin Source Paths

| Agent | Active Plugin Location | Workspace Repo Mirror |
|-------|----------------------|-----------------------|
| **Claude Code** | `~/.claude/plugins/elephantbroker-memory/` (marketplace 安装) | `plugins/claude-code/` |
| **AGY CLI** | `~/.gemini/config/plugins/elephantbroker-memory/` | `plugins/antigravity-cli/` |
| **Hermes** | `~/.hermes/plugins/memory/elephantbroker/` | `plugins/hermes-agent/` |
| **OpenCode** | `~/.config/opencode/plugins/elephantbroker-memory.ts` | `plugins/opencode/elephantbroker-memory.ts` |
| **OpenClaw** | `~/.openclaw/extensions/elephantbroker-memory/` and `~/.openclaw/extensions/elephantbroker-context/` | `plugins/openclaw/memory/`, `plugins/openclaw/context/` |

## EB Runtime Services

| Service | Status | Port | Startup |
|---------|--------|------|---------|
| EB Cognitive Runtime | `active` | 8420 | systemd `elephantbroker.service` (system, enabled) |
| HITL Middleware | `active` | 8421 | systemd `elephantbroker-hitl.service` (user, enabled, Linger) |
| Qdrant | running | 16333 | docker/eb_infra |
| Neo4j | running | 17687 | docker/eb_infra |
| Redis | running | 16379 | docker/eb_infra |
| HITL DB (SQLite) | active | — | `/var/lib/elephantbroker/data/` |
| vLLM Embedding | running | 8001 | docker (`vllm-embedding`, no alias) |

## Actor Registration State (Neo4j)

```
AgentIdentity: 7 total
  gw-enterprise-prod | claude-code-agent  | prod:claude-code-agent
  gw-enterprise-prod | agy-cli-agent      | prod:agy-cli-agent
  gw-enterprise-prod | hermes-agent       | prod:hermes-agent
  gw-enterprise-prod | opencode-agent     | prod:opencode-agent
  gw-enterprise-prod | openclaw-agent     | prod:openclaw-agent
  gw-enterprise-prod | main               | prod:main          (legacy)
  gw-enterprise-prod | default            | gw-enter:default   (legacy)
```

## Config Snapshot Files

| File | Source |
|------|--------|
| `configs/claude-code/env.json` | `~/.claude/settings.json` EB env subset |
| `configs/claude-code/plugin-data-config.json` | `~/.claude/elephantbroker/config.json` |
| `configs/agy-cli/config.json` | `~/.elephantbroker/config.json` |
| `configs/hermes/elephantbroker.json` | `~/.hermes/elephantbroker.json` |
| `configs/opencode/runtime.json` | OpenCode plugin config + runtime vars |
| `configs/opencode/launcher-env.sh` | `~/.local/bin/opencode` env content |
| `configs/openclaw/runtime.json` | OpenClaw runtime vars |
| `configs/openclaw/launcher-env.sh` | `~/.local/bin/openclaw` env content |

## Key Env Vars (Global)

```bash
EB_MODE=true
EB_RUNTIME_URL=http://localhost:8420
EB_GATEWAY_ID=gw-enterprise-prod
EB_GATEWAY_SHORT_NAME=prod
COGNEE_SERVICE_URL=http://localhost:8420
COGNEE_PLUGIN_DATASET=elephantbroker
```

## Quick Reference

### Check EB health
```bash
curl -s http://localhost:8420/health/ | jq
curl -s --max-time 60 http://localhost:8420/health/ready | jq
```

### Check memory status
```bash
curl -s http://localhost:8420/memory/status | jq
```

### Verify actor registrations (via Neo4j)
```bash
python3 -c "
from neo4j import GraphDatabase
d = GraphDatabase.driver('bolt://localhost:17687', auth=('neo4j','elephant_dev'))
with d.session() as s:
    for r in s.run('MATCH (n:ActorDataPoint) RETURN n.display_name, n.gateway_id ORDER BY n.created_at'):
        print(f'{r[1]} | {r[0]}')
d.close()
"
```

### Restart HITL
```bash
systemctl --user restart elephantbroker-hitl.service
```

### Restart EB Runtime
```bash
sudo systemctl restart elephantbroker.service
```
