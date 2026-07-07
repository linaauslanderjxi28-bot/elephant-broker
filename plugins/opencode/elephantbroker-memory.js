"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.ElephantBrokerMemory = void 0;
const node_crypto_1 = __importDefault(require("node:crypto"));
const plugin_1 = require("@opencode-ai/plugin");
function isTextMessagePart(part) {
    if (!part || typeof part !== "object")
        return false;
    const candidate = part;
    return candidate.type === "text" && typeof candidate.text === "string";
}
function isUUID(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
function nonBlank(value) {
    return typeof value === "string" && value.trim().length > 0;
}
function sessionIdFromKey(sessionKey) {
    const hex = node_crypto_1.default.createHash("sha256").update(String(sessionKey), "utf8").digest("hex").slice(0, 32);
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}
const AUDIT_CATEGORIES = new Set(["tool-call", "conversation", "todowrite"]);
function filterAuditResults(results, includeAudit) {
    if (includeAudit)
        return results;
    return results.filter((result) => !AUDIT_CATEGORIES.has(result.category));
}
class EBClient {
    baseUrl;
    gatewayId;
    sessionKey = "agent:main:main";
    sessionId = node_crypto_1.default.randomUUID();
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
        if (this.agentId)
            h["X-EB-Agent-ID"] = this.agentId;
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
        if (!res.ok) {
            if (statusOK !== undefined && res.status === statusOK)
                return null;
            throw new Error(`EB ${method} ${path} failed: ${res.status}`);
        }
        const text = await res.text();
        return text ? JSON.parse(text) : null;
    }
    async search(query, opts) {
        const scope = nonBlank(opts?.scope) ? opts.scope.trim() : "";
        const includeAudit = opts?.include_audit ?? false;
        const results = await this.req("POST", "/memory/search", {
            query,
            max_results: opts?.max_results ?? 5,
            min_score: opts?.min_score ?? 0,
            auto_recall: opts?.auto_recall ?? false,
            include_audit: includeAudit,
            ...(scope ? { scope } : {}),
            ...(scope === "session" ? { session_key: this.sessionKey, session_id: this.sessionId } : {}),
            ...(nonBlank(opts?.entity_type) ? { entity_type: opts.entity_type.trim() } : {}),
        });
        return filterAuditResults(results, includeAudit);
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
            const res = await fetch(`${this.baseUrl}/memory/${id}`, {
                method: "DELETE",
                headers: this.headers(),
                signal: AbortSignal.timeout(30000),
            });
            return res.ok;
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
    const log = (level, message, extra) => {
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
            return 0;
        flushRunning = true;
        let flushed = 0;
        try {
            while (messageBuffer.length > 0) {
                const batch = messageBuffer.splice(0);
                const messages = batch.map((msg) => ({ role: msg.role, content: msg.text }));
                const ingested = await ebClient.ingestTurn(messages);
                if (ingested) {
                    flushed += batch.length;
                    continue;
                }
                for (const msg of batch) {
                    try {
                        await ebClient.store(`[${msg.role}] ${msg.text}`, { category: "conversation", scope: "session" });
                        flushed += 1;
                    }
                    catch {
                        const stored = await ebClient.ingestMessages([{ role: msg.role, content: msg.text }]);
                        if (stored)
                            flushed += 1;
                        else
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
        return flushed;
    }
    function setHookSession(sessionID) {
        if (sessionID)
            ebClient.setSession(`opencode:${sessionID}`, sessionID);
    }
    function formatRecall(results) {
        return [
            "ElephantBroker recalled context:",
            ...results.map((result) => `- [${result.category}] ${result.text}`),
        ].join("\n");
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
                        .describe("Scope filter (global, organization, team, actor, session)"),
                    entity_type: plugin_1.tool.schema.string().optional()
                        .describe("Entity type filter: Product, Supplier, MarketSignal, ResearchDecision, Prospect, CustomsRecord, Deal, FinancialReport, Invoice, Contract, Document"),
                    include_audit: plugin_1.tool.schema.boolean().optional().default(false)
                        .describe("Include tool-call/conversation/todowrite audit records. Defaults to false."),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured. Set EB_GATEWAY_ID and EB_RUNTIME_URL env vars.";
                    try {
                        const results = await ebClient.search(args.query, {
                            max_results: Math.min(args.max_results ?? 5, 20),
                            min_score: args.min_score ?? 0,
                            scope: args.scope,
                            entity_type: args.entity_type,
                            include_audit: args.include_audit ?? false,
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
                        .describe("Visibility scope: session, actor, team, organization, or global"),
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
                    scope: plugin_1.tool.schema.string().optional().describe("New scope: session, actor, team, organization, or global"),
                    memory_class: plugin_1.tool.schema.string().optional().describe("New memory class, e.g. episodic, semantic, procedural"),
                    target_actor_ids: plugin_1.tool.schema.array(plugin_1.tool.schema.string()).optional()
                        .describe("Actor UUIDs this fact targets"),
                    decision_domain: plugin_1.tool.schema.string().optional().describe("Decision domain tag"),
                    goal_ids: plugin_1.tool.schema.array(plugin_1.tool.schema.string()).optional()
                        .describe("New goal UUIDs this fact relates to"),
                    archived: plugin_1.tool.schema.boolean().optional()
                        .describe("Archive/unarchive this fact"),
                    autorecall_blacklisted: plugin_1.tool.schema.boolean().optional()
                        .describe("Exclude/include this fact from automatic recall"),
                },
                async execute(args) {
                    if (!gatewayId)
                        return "EB_GATEWAY_ID not configured.";
                    const updates = {};
                    if (nonBlank(args.text))
                        updates.text = args.text.trim();
                    if (args.confidence !== undefined)
                        updates.confidence = args.confidence;
                    if (nonBlank(args.category))
                        updates.category = args.category.trim();
                    if (nonBlank(args.scope))
                        updates.scope = args.scope.trim();
                    if (nonBlank(args.memory_class))
                        updates.memory_class = args.memory_class.trim();
                    if (Array.isArray(args.target_actor_ids)) {
                        for (const actorId of args.target_actor_ids) {
                            if (!isUUID(actorId))
                                return `Invalid target_actor_id (must be UUID): ${actorId}`;
                        }
                        updates.target_actor_ids = args.target_actor_ids;
                    }
                    if (nonBlank(args.decision_domain))
                        updates.decision_domain = args.decision_domain.trim();
                    if (Array.isArray(args.goal_ids)) {
                        for (const goalId of args.goal_ids) {
                            if (!isUUID(goalId))
                                return `Invalid goal_id (must be UUID): ${goalId}`;
                        }
                        updates.goal_ids = args.goal_ids;
                    }
                    if (args.archived !== undefined)
                        updates.archived = args.archived;
                    if (args.autorecall_blacklisted !== undefined)
                        updates.autorecall_blacklisted = args.autorecall_blacklisted;
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
            setHookSession(_input.sessionID);
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
        "tool.execute.after": async (input, output) => {
            if (!gatewayId)
                return;
            setHookSession(input.sessionID);
            const renderedOutput = typeof output.output === "string" ? output.output : JSON.stringify(output.output);
            const text = [
                `Tool ${input.tool} completed (call ${input.callID}).`,
                output.title ? `Title: ${output.title}` : "",
                `Args: ${JSON.stringify(input.args ?? {})}`,
                renderedOutput ? `Output: ${renderedOutput}` : "",
            ].filter(Boolean).join("\n");
            try {
                await ebClient.store(text, { category: "tool-call", scope: "session" });
            }
            catch (e) {
                log("error", "tool audit capture failed", { error: e instanceof Error ? e.message : String(e) });
            }
        },
        "experimental.session.compacting": async (input, output) => {
            if (!gatewayId)
                return;
            setHookSession(input.sessionID);
            const flushed = await flushMessages();
            if (flushed > 0) {
                output.context.push(`ElephantBroker flushed ${flushed} buffered message(s) before compaction.`);
            }
        },
        "experimental.chat.system.transform": async (input, output) => {
            if (!gatewayId)
                return;
            setHookSession(input.sessionID);
            try {
                const results = await ebClient.search("current session context", {
                    max_results: 5,
                    min_score: 0,
                    auto_recall: true,
                    scope: "session",
                });
                if (results?.length)
                    output.system.push(formatRecall(results));
            }
            catch (e) {
                log("error", "system recall failed", { error: e instanceof Error ? e.message : String(e) });
            }
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
