"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ElephantBrokerMemory = void 0;
const node_crypto_1 = require("node:crypto");
const plugin_1 = require("@opencode-ai/plugin");
function isTextMessagePart(part) {
    if (!part || typeof part !== "object")
        return false;
    const candidate = part;
    return candidate.type === "text" && typeof candidate.text === "string";
}
function createLogger(client) {
    return (level, message, extra) => {
        const appLog = client?.app?.log;
        if (appLog) {
            void appLog({ body: { service: "elephantbroker", level, message, extra } }).catch(() => { });
            return;
        }
        const line = `[EB] ${message}`;
        if (level === "error")
            console.error(line, extra ?? "");
        else if (level === "warn")
            console.warn(line, extra ?? "");
        else
            console.info(line, extra ?? "");
    };
}
function isUUID(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
function sessionIdFromKey(sessionKey) {
    const hex = node_crypto_1.createHash("sha256").update(String(sessionKey), "utf8").digest("hex").slice(0, 32);
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}
class EBClient {
    baseUrl;
    gatewayId;
    sessionKey = "agent:main:main";
    sessionId = crypto.randomUUID();
    agentKey = "";
    agentId = "";
    actorId = "";
    profileName = "";
    gatewayShortName = "";
    constructor(baseUrl, gatewayId, profileName, agentKey, agentId, actorId, gatewayShortName) {
        this.baseUrl = baseUrl.replace(/\/+$/, "");
        this.gatewayId = gatewayId;
        if (profileName)
            this.profileName = profileName;
        if (agentKey)
            this.agentKey = agentKey;
        if (agentId)
            this.agentId = agentId;
        if (actorId)
            this.actorId = actorId;
        if (gatewayShortName)
            this.gatewayShortName = gatewayShortName;
    }
    setProfileName(name) { this.profileName = name; }
    setSession(sessionKey, sessionId) {
        this.sessionKey = sessionKey;
        this.sessionId = isUUID(sessionId) ? sessionId : sessionIdFromKey(`${sessionKey}:${sessionId}`);
    }
    headers() {
        const h = {
            "Content-Type": "application/json",
            "X-EB-Gateway-ID": this.gatewayId,
            "X-EB-Session-Key": this.sessionKey,
        };
        if (this.agentKey)
            h["X-EB-Agent-Key"] = this.agentKey;
        if (this.actorId)
            h["X-EB-Actor-Id"] = this.actorId;
        const authToken = (process.env.EB_AUTH_TOKEN || "").trim();
        if (authToken)
            h["X-EB-Auth-Token"] = authToken;
        return h;
    }
    async req(method, path, body, statusOK) {
        const url = `${this.baseUrl}${path}`;
        const res = await fetch(url, {
            method,
            headers: this.headers(),
            body: body ? JSON.stringify(body) : undefined,
            signal: AbortSignal.timeout(30000),
        });
        if (statusOK !== undefined && res.status === statusOK)
            return null;
        if (!res.ok) {
            // For 404/403/409, return null instead of throwing
            if (res.status === 404 || res.status === 403 || res.status === 409)
                return null;
            throw new Error(`EB ${method} ${path} failed: ${res.status}`);
        }
        const text = await res.text();
        return text ? JSON.parse(text) : null;
    }
    async search(query, opts) {
        return this.req("POST", "/memory/search", {
            query,
            max_results: opts?.max_results ?? 5,
            min_score: opts?.min_score ?? 0,
            auto_recall: opts?.auto_recall ?? false,
            session_key: this.sessionKey,
            session_id: this.sessionId,
            ...(opts?.entity_type ? { entity_type: opts.entity_type } : {}),
            ...(this.profileName ? { profile_name: this.profileName } : {}),
        });
    }
    async searchGlobal(query, opts) {
        return this.req("POST", "/memory/search", {
            query,
            max_results: opts?.max_results ?? 10,
            min_score: opts?.min_score ?? 0,
            auto_recall: opts?.auto_recall ?? false,
            scope: "global",
            ...(opts?.session_key ? { session_key: opts.session_key } : {}),
            ...(opts?.entity_type ? { entity_type: opts.entity_type } : {}),
            ...(this.profileName ? { profile_name: this.profileName } : {}),
        });
    }
    async store(text, opts) {
        return this.req("POST", "/memory/store", {
            fact: {
                text,
                category: opts?.category ?? "general",
                scope: opts?.scope ?? "session",
                confidence: opts?.confidence ?? 1.0,
                ...(opts?.goal_ids?.length ? { goal_ids: opts.goal_ids } : {}),
                ...(opts?.decision_status ? { decision_status: opts.decision_status } : {}),
            },
            session_key: this.sessionKey,
            session_id: this.sessionId,
            ...(this.profileName ? { profile_name: this.profileName } : {}),
        }, 409);
    }
    async getById(id) {
        return this.req("GET", `/memory/${id}`, undefined, 404);
    }
    async forget(id) {
        try {
            await this.req("DELETE", `/memory/${id}`);
            return true;
        }
        catch {
            return false;
        }
    }
    async update(id, updates) {
        return this.req("PATCH", `/memory/${id}`, updates, 404);
    }
    async inspectActor(actorId, opts) {
        const actor = await this.req("GET", `/actors/${encodeURIComponent(actorId)}`, undefined, 404);
        const result = { actor };
        if (opts?.include_relationships) {
            result.relationships = await this.req("GET", `/actors/${encodeURIComponent(actorId)}/relationships`, undefined, 404);
        }
        if (opts?.include_authority_chain) {
            result.authority_chain = await this.req("GET", `/actors/${encodeURIComponent(actorId)}/authority-chain`, undefined, 404);
        }
        return result;
    }
    async getClaim(claimId) {
        return this.req("GET", `/claims/${encodeURIComponent(claimId)}`, undefined, 404);
    }
    async lookupProcedureAudit(args) {
        if (args.action_id) {
            return this.req("GET", `/procedures/audit/action/${encodeURIComponent(args.action_id)}`);
        }
        const params = new URLSearchParams({ lineage_ref: args.lineage_ref ?? "" });
        return this.req("GET", `/procedures/audit/lineage?${params}`);
    }
    async sessionStart() {
        await this.req("POST", "/sessions/start", {
            session_key: this.sessionKey,
            session_id: this.sessionId,
            gateway_id: this.gatewayId,
            agent_id: this.agentId,
            agent_key: this.agentKey,
            gateway_short_name: this.gatewayShortName,
        });
    }
    async sessionEnd() {
        await this.req("POST", "/sessions/end", {
            session_key: this.sessionKey,
            session_id: this.sessionId,
            gateway_id: this.gatewayId,
            agent_key: this.agentKey,
        });
    }
    async ingestTurn(messages) {
        try {
            return await this.req("POST", "/memory/ingest-turn", {
                session_key: this.sessionKey,
                session_id: this.sessionId,
                profile_name: this.profileName || "coding",
                messages,
            });
        }
        catch {
            return null;
        }
    }
    async ingestMessages(messages) {
        try {
            await this.req("POST", "/memory/ingest-messages", {
                session_key: this.sessionKey,
                session_id: this.sessionId,
                profile_name: this.profileName || "coding",
                messages,
            });
            return true;
        }
        catch {
            return false;
        }
    }
}
// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------
const ElephantBrokerMemory = async ({ client } = {}) => {
    const log = createLogger(client);
    const baseUrl = process.env.EB_SERVICE_URL || process.env.EB_RUNTIME_URL || process.env.COGNEE_SERVICE_URL || "http://localhost:8420";
    const gatewayId = process.env.EB_GATEWAY_ID ?? "";
    const profileName = process.env.EB_PROFILE ?? "coding";
    const agentId = process.env.EB_AGENT_NAME ?? process.env.COGNEE_AGENT_NAME ?? "";
    const agentKey = process.env.EB_AGENT_KEY ?? agentId;
    const actorId = process.env.EB_ACTOR_ID ?? "";
    const gatewayShortName = process.env.EB_GATEWAY_SHORT_NAME ?? "";
    if (!gatewayId) {
        log("warn", "EB_GATEWAY_ID not set; plugin loaded but inactive");
    }
    const ebClient = new EBClient(baseUrl, gatewayId, profileName, agentKey, agentId, actorId, gatewayShortName);
    // ---------------------------------------------------------------------------
    // Conversation auto-capture buffer
    // ---------------------------------------------------------------------------
    const messageBuffer = [];
    let flushTimer = null;
    let flushRunning = false;
    function scheduleFlush(delayMs = 2000) {
        if (flushTimer)
            clearTimeout(flushTimer);
        flushTimer = setTimeout(async () => {
            flushTimer = null;
            await flushMessages();
        }, delayMs);
    }
    async function flushMessages() {
        if (flushRunning)
            return;
        flushRunning = true;
        try {
            while (messageBuffer.length > 0) {
                const batch = messageBuffer.splice(0);
                const messages = batch.map((msg) => ({ role: msg.role, content: msg.text }));
                const ingested = await ebClient.ingestTurn(messages);
                if (ingested)
                    continue;
                for (const msg of batch) {
                    try {
                            await ebClient.store(`[${msg.role}] ${msg.text}`, { category: "conversation", scope: "session" });
                    }
                    catch {
                            const stored = await ebClient.ingestMessages([{ role: msg.role, content: msg.text }]);
                        if (!stored)
                            log("error", "message capture failed");
                    }
                }
            }
        }
        catch (e) {
            const message = e instanceof Error ? e.message : String(e);
        log("error", "flush failed", { error: message });
        }
        finally {
            flushRunning = false;
        }
    }
    const seenMessages = new Set();
    const assistantTexts = new Map();
    return {
        tool: {
            memory_search: (0, plugin_1.tool)({
                description: "Search ElephantBroker memory for relevant facts and context. Returns knowledge from current and past sessions.",
                args: {
                    query: plugin_1.tool.schema.string().describe("Natural language search query"),
                    max_results: plugin_1.tool.schema.number().optional().default(5)
                        .describe("Maximum results to return (1-20)"),
                    min_score: plugin_1.tool.schema.number().optional().default(0)
                        .describe("Minimum relevance score (0-1)"),
                    scope: plugin_1.tool.schema.string().optional()
                        .describe("Scope filter (global, session, actor)"),
                    entity_type: plugin_1.tool.schema.string().optional()
                        .describe("Entity type filter: Product, Supplier, MarketSignal, ResearchDecision, Prospect, CustomsRecord, Deal, FinancialReport, Invoice, Contract, Document"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured. Set EB_GATEWAY_ID and EB_RUNTIME_URL env vars.";
                    try {
                    const results = await ebClient.search(args.query, {
                            max_results: Math.min(args.max_results ?? 5, 20),
                            min_score: args.min_score ?? 0,
                            entity_type: args.entity_type,
                        });
                        if (!results || results.length === 0)
                            return "No relevant memories found.";
                        return [
                            `Found ${results.length} memory result(s):`,
                            "",
                            ...results.map((r, i) => `[${i + 1}] [${r.category}] ${r.text} (confidence: ${r.confidence.toFixed(2)}, score: ${r.score.toFixed(3)})`),
                        ].join("\n");
                    }
                    catch (e) {
                        return `Memory search failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_search_global: (0, plugin_1.tool)({
                description: "Search the global ElephantBroker knowledge base. Use this for data imported from scrapling, doc-ingestor, or other non-session pipelines.",
                args: {
                    query: plugin_1.tool.schema.string().describe("Natural language search query"),
                    max_results: plugin_1.tool.schema.number().optional().default(20)
                        .describe("Maximum global results to return (1-30)"),
                    min_score: plugin_1.tool.schema.number().optional().default(0)
                        .describe("Minimum relevance score (0-1)"),
                    session_key: plugin_1.tool.schema.string().optional()
                        .describe("Optional global session key filter, e.g. scrapling:example-com or doc-ingestor:0-inbox"),
                    category: plugin_1.tool.schema.string().optional()
                        .describe("Optional category filter, e.g. 'project' for doc-ingestor, 'finance' for financial data"),
                    entity_type: plugin_1.tool.schema.string().optional()
                        .describe("Optional entity type filter: FinancialReport, Invoice, Contract, Document"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured. Set EB_GATEWAY_ID and EB_RUNTIME_URL env vars.";
                    try {
                    const results = await ebClient.searchGlobal(args.query, {
                            max_results: Math.min(args.max_results ?? 20, 30),
                            min_score: args.min_score ?? 0,
                            session_key: args.session_key,
                            entity_type: args.entity_type,
                        });
                        if (!results || results.length === 0)
                            return "No relevant global memories found.";
                        // Apply category filter if specified (client-side since EB doesn't support it natively)
                        const filtered = args.category
                            ? results.filter((r) => r.category === args.category)
                            : results;
                        if (filtered.length === 0)
                            return `No global memories found matching category '${args.category}'.`;
                        return [
                            `Found ${filtered.length} global memory result(s)${args.category ? ` (category: ${args.category})` : ""}:`,
                            "",
                            ...filtered.map((r, i) => `[${i + 1}] [${r.category}] ${r.text} (confidence: ${r.confidence.toFixed(2)}, score: ${r.score.toFixed(3)})`),
                        ].join("\n");
                    }
                    catch (e) {
                        return `Global memory search failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_store: (0, plugin_1.tool)({
                description: "Store a fact or piece of knowledge into ElephantBroker memory for future recall.",
                args: {
                    text: plugin_1.tool.schema.string().describe("The fact or knowledge to store"),
                    category: plugin_1.tool.schema.string().optional().default("general")
                        .describe("Category tag (e.g. 'code', 'architecture', 'user-preference')"),
                    scope: plugin_1.tool.schema.string().optional().default("session")
                        .describe("Visibility scope: session, actor, team, or global"),
                    goal_ids: plugin_1.tool.schema.array(plugin_1.tool.schema.string()).optional()
                        .describe("Optional fact IDs this fact relates to"),
                    decision_status: plugin_1.tool.schema.string().optional()
                        .describe("Decision status: proposed, approved, rejected, actioned"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const result = await ebClient.store(args.text, {
                            category: args.category,
                            scope: args.scope,
                            goal_ids: args.goal_ids,
                            decision_status: args.decision_status,
                        });
                        if (!result)
                            return "Memory stored (deduplicated — similar fact already exists).";
                        return `Stored memory "${args.text.substring(0, 60)}..." (id: ${result.id})`;
                    }
                    catch (e) {
                        return `Memory store failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_decide: (0, plugin_1.tool)({
                description: "Record a decision or finding linked to source facts. Use when you discover an anomaly, make a judgment, or need to track an action based on retrieved data.",
                args: {
                    text: plugin_1.tool.schema.string().describe("The decision, finding, or action description"),
                    decision_status: plugin_1.tool.schema.string().optional().default("proposed")
                        .describe("Decision status: proposed, approved, rejected, actioned"),
                    source_fact_ids: plugin_1.tool.schema.array(plugin_1.tool.schema.string()).optional()
                        .describe("Source fact IDs this decision is based on"),
                    scope: plugin_1.tool.schema.string().optional().default("team"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const result = await ebClient.store(args.text, {
                            category: "decision",
                            scope: args.scope,
                            decision_status: args.decision_status,
                            goal_ids: args.source_fact_ids,
                        });
                        if (!result)
                            return "Decision stored (deduplicated).";
                        return `Decision recorded: "${args.text.substring(0, 60)}..." (status: ${args.decision_status}, id: ${result.id})`;
                    }
                    catch (e) {
                        return `Decision store failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_get: (0, plugin_1.tool)({
                description: "Retrieve a specific memory fact by its ID from ElephantBroker.",
                args: {
                    id: plugin_1.tool.schema.string().describe("Fact ID to retrieve"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const fact = await ebClient.getById(args.id);
                        if (!fact)
                            return `Fact not found: ${args.id}`;
                        return [
                            `ID: ${fact.id}`,
                            `Text: ${fact.text}`,
                            `Category: ${fact.category}`,
                            `Scope: ${fact.scope}`,
                            `Confidence: ${fact.confidence}`,
                            `Memory Class: ${fact.memory_class}`,
                            `Created: ${fact.created_at}`,
                            `Updated: ${fact.updated_at}`,
                            `Use Count: ${fact.use_count}`,
                        ].join("\n");
                    }
                    catch (e) {
                        return `Memory retrieve failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_forget: (0, plugin_1.tool)({
                description: "Delete a memory fact from ElephantBroker by its ID.",
                args: {
                    id: plugin_1.tool.schema.string().describe("Fact ID to delete"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const ok = await ebClient.forget(args.id);
                        return ok ? `Memory ${args.id} deleted.` : `Failed to delete memory ${args.id} (not found or permission denied).`;
                    }
                    catch (e) {
                        return `Memory delete failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            memory_update: (0, plugin_1.tool)({
                description: "Update an existing memory fact's text or properties in ElephantBroker.",
                args: {
                    id: plugin_1.tool.schema.string().describe("Fact ID to update"),
                    text: plugin_1.tool.schema.string().optional().describe("New text content"),
                    confidence: plugin_1.tool.schema.number().optional().describe("New confidence value (0-1)"),
                    category: plugin_1.tool.schema.string().optional().describe("New category"),
                    decision_status: plugin_1.tool.schema.string().optional()
                        .describe("Decision status: proposed, approved, rejected, actioned"),
                    entity_type: plugin_1.tool.schema.string().optional()
                        .describe("Entity type: FinancialReport, Invoice, Contract, Document"),
                    goal_ids: plugin_1.tool.schema.array(plugin_1.tool.schema.string()).optional()
                        .describe("New goal IDs this fact relates to"),
                    archived: plugin_1.tool.schema.boolean().optional()
                        .describe("Archive/unarchive this fact"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    const updates = {};
                    if (args.text !== undefined)
                        updates.text = args.text;
                    if (args.confidence !== undefined)
                        updates.confidence = args.confidence;
                    if (args.category !== undefined)
                        updates.category = args.category;
                    if (args.decision_status !== undefined)
                        updates.decision_status = args.decision_status;
                    if (args.entity_type !== undefined)
                        updates.entity_type = args.entity_type;
                    if (args.goal_ids !== undefined)
                        updates.goal_ids = args.goal_ids;
                    if (args.archived !== undefined)
                        updates.archived = args.archived;
                    if (Object.keys(updates).length === 0)
                        return "Nothing to update.";
                    try {
                    const result = await ebClient.update(args.id, updates);
                        if (!result)
                            return `Fact not found: ${args.id}`;
                        return `Memory ${args.id} updated successfully.`;
                    }
                    catch (e) {
                        return `Memory update failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            actor_inspect: (0, plugin_1.tool)({
                description: "Read actor details with optional relationships and authority-chain context.",
                args: {
                    actor_id: plugin_1.tool.schema.string().describe("Actor UUID to inspect"),
                    include_relationships: plugin_1.tool.schema.boolean().optional().default(false)
                        .describe("Include actor relationship records"),
                    include_authority_chain: plugin_1.tool.schema.boolean().optional().default(false)
                        .describe("Include actor authority-chain records"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const result = await ebClient.inspectActor(args.actor_id, {
                            include_relationships: args.include_relationships,
                            include_authority_chain: args.include_authority_chain,
                        });
                        return JSON.stringify(result, null, 2);
                    }
                    catch (e) {
                        return `Actor inspect failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            claim_get: (0, plugin_1.tool)({
                description: "Read a claim and its current verification state by claim ID.",
                args: {
                    claim_id: plugin_1.tool.schema.string().describe("Claim UUID to read"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    try {
                    const result = await ebClient.getClaim(args.claim_id);
                        return JSON.stringify(result, null, 2);
                    }
                    catch (e) {
                        return `Claim get failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
            procedure_audit_lookup: (0, plugin_1.tool)({
                description: "Read procedure audit events by action ID or lineage reference.",
                args: {
                    action_id: plugin_1.tool.schema.string().optional().describe("Action ID to look up"),
                    lineage_ref: plugin_1.tool.schema.string().optional().describe("Lineage reference to look up"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    if (Boolean(args.action_id) === Boolean(args.lineage_ref)) {
                        return "Provide exactly one of action_id or lineage_ref.";
                    }
                    try {
                    const result = await ebClient.lookupProcedureAudit({
                            action_id: args.action_id,
                            lineage_ref: args.lineage_ref,
                        });
                        return JSON.stringify(result, null, 2);
                    }
                    catch (e) {
                        return `Procedure audit lookup failed: ${e instanceof Error ? e.message : String(e)}`;
                    }
                },
            }),
        },
        "chat.message": async (_input, output) => {
            if (!gatewayId)
                return;
            const msgId = _input.messageID ?? output.message.id;
            if (!msgId || seenMessages.has(msgId))
                return;
            seenMessages.add(msgId);
            const textParts = output.parts.filter(isTextMessagePart);
            const content = textParts.map((p) => p.text).join("\n").trim();
            if (!content)
                return;
            messageBuffer.push({
                role: "user",
                text: content,
                timestamp: Date.now(),
            });
            scheduleFlush();
        },
        event: async ({ event }) => {
            if (!gatewayId)
                return;
            const ev = event;
            if (ev.type === "session.created") {
                const sessionID = ev.properties?.sessionID;
                if (sessionID) {
                    ebClient.setSession(`opencode:${sessionID}`, String(sessionID));
                }
                ebClient.sessionStart().catch((e) => log("error", "sessionStart failed", { error: e instanceof Error ? e.message : String(e) }));
            }
            if (ev.type === "session.idle" || ev.type === "session.deleted") {
                await flushMessages();
                ebClient.sessionEnd().catch((e) => log("error", "sessionEnd failed", { error: e instanceof Error ? e.message : String(e) }));
            }
            if (ev.type === "session.next.text.ended") {
                const sessionID = ev.properties?.sessionID;
                const text = ev.properties?.text;
                if (text) {
                    const key = String(sessionID ?? "default");
                    const parts = assistantTexts.get(key) || [];
                    parts.push(String(text));
                    assistantTexts.set(key, parts);
                }
            }
            if (ev.type === "session.next.step.ended") {
                const key = String(ev.properties?.sessionID ?? "default");
                const parts = assistantTexts.get(key);
                if (parts && parts.length > 0) {
                    const fullText = parts.join("\n").trim();
                    if (fullText) {
                        messageBuffer.push({
                            role: "assistant",
                            text: fullText,
                            timestamp: Date.now(),
                        });
                        scheduleFlush();
                    }
                    assistantTexts.delete(key);
                }
            }
        },
    };
};
exports.ElephantBrokerMemory = ElephantBrokerMemory;
exports.default = exports.ElephantBrokerMemory;
