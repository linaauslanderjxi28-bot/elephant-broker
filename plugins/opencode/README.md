# ElephantBroker Memory Plugin for OpenCode

为 OpenCode 提供持久化记忆能力，通过 ElephantBroker 后端实现跨会话的知识存储与检索。

## 功能

- `memory_search` — 语义搜索已有记忆，支持 `session`、`actor`、`team`、`organization`、`global` 等 scope
- `memory_search_global` — 搜索全局记忆库
- `memory_store` — 存储事实/知识到记忆，支持写入 `team`、`organization` 等共享 scope
- `memory_get` — 按 ID 获取记忆详情
- `memory_forget` — 按 ID 删除记忆
- `memory_update` — 更新记忆内容或置信度
- 会话生命周期管理（自动开始/结束会话）

## 安装

### 前置条件

确保 ElephantBroker 服务正在运行（默认 `http://localhost:8420`）。

### 插件安装

将 `elephantbroker-memory.ts` 放到 OpenCode 的 `plugins/` 目录下：

```bash
mkdir -p ~/.config/opencode/plugins
cp elephantbroker-memory.ts ~/.config/opencode/plugins/
```

或在 opencode.json 中直接引用插件路径。

### 环境变量配置

在 `~/.bashrc`（或对应 shell 配置）中添加：

```bash
export EB_MODE=true
export EB_RUNTIME_URL="http://localhost:8420"
export EB_GATEWAY_ID="gw-enterprise-prod"
export EB_ACTOR_ID="registered-authority-actor-uuid"
```

| 变量 | 说明 | 默认值 |
|---|---|---|
| `EB_RUNTIME_URL` | ElephantBroker 服务地址 | `http://localhost:8420` |
| `EB_GATEWAY_ID` | 网关 ID | 空（不填则插件静默不激活） |
| `EB_PROFILE` | 检索/存储使用的 profile | `coding` |
| `EB_AUTH_TOKEN` | Runtime auth token；启用鉴权时必填 | 空 |
| `EB_ACTOR_ID` | 已注册 actor UUID；调用 `memory_store`、`memory_update`、`memory_forget` 时需要足够 authority | 空 |

## 验证

启动 OpenCode 后，使用 `memory_store` 存储一条测试内容，然后用 `memory_search` 搜索即可验证是否正常工作。
