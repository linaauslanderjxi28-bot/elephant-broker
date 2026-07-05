import { type Plugin, tool } from "@opencode-ai/plugin";

declare const process: { env: Record<string, string | undefined> };

// ---------------------------------------------------------------------------
// Minimal ElephantBroker HTTP client — no external dependencies, uses fetch
// ---------------------------------------------------------------------------

interface EBSearchResult {
  id: string;
  text: string;
  category: string;
  scope: string;
  confidence: number;
  memory_class: string;
  session_key?: string;
  session_id?: string;
  source_actor_id?: string;
  created_at: string;
  updated_at: string;
  score: number;
  source: string;
}

interface EBFact {
  id: string;
  text: string;
  category: string;
  scope: string;
  confidence: number;
  memory_class: string;
  session_key?: string;
  session_id?: string;
  created_at: string;
  updated_at: string;
  use_count: number;
  gateway_id?: string;
}

interface EBIngestTurnResult {
  facts_extracted?: EBFact[];
  facts_stored?: number;
  facts_superseded?: number;
  trace_event_id?: string;
}

type UUID = `${string}-${string}-${string}-${string}-${string}`;

type BufferedMessage = {
  role: string;
  text: string;
  timestamp: number;
  id?: string;
};

type TextMessagePart = {
  type: "text";
  text: string;
};

function isTextMessagePart(part: unknown): part is TextMessagePart {
  if (!part || typeof part !== "object") return false;
  const candidate = part as Record<string, unknown>;
  return candidate.type === "text" && typeof candidate.text === "string";
}

function isUUID(value: string): value is UUID {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function sessionIdFromKey(sessionKey: string): UUID {
  const hash = (seed: number) => {
    let value = seed >>> 0;
    for (let i = 0; i < sessionKey.length; i += 1) {
      value ^= sessionKey.charCodeAt(i);
      value = Math.imul(value, 16777619) >>> 0;
    }
    return value.toString(16).padStart(8, "0");
  };
  const hex = `${hash(0x811c9dc5)}${hash(0x01000193)}${hash(0x9e3779b9)}${hash(0x85ebca6b)}`;
  const version = `4${hex.slice(13, 16)}`;
  const variant = ((parseInt(hex.slice(16, 18), 16) & 0x3f) | 0x80).toString(16).padStart(2, "0");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${version}-${variant}${hex.slice(18, 20)}-${hex.slice(20, 32)}`;
}

class EBClient {
  private baseUrl: string;
  private gatewayId: string;
  private sessionKey = "agent:main:main";
  private sessionId = crypto.randomUUID();
  private agentKey = "";
  private agentId = "";
  private actorId = "";
  private profileName = "";
  private gatewayShortName = "";

  constructor(
    baseUrl: string,
    gatewayId: string,
    profileName?: string,
    agentKey?: string,
    agentId?: string,
    actorId?: string,
    gatewayShortName?: string,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.gatewayId = gatewayId;
    if (profileName) this.profileName = profileName;
    if (agentKey) this.agentKey = agentKey;
    if (agentId) this.agentId = agentId;
    if (actorId) this.actorId = actorId;
    if (gatewayShortName) this.gatewayShortName = gatewayShortName;
  }

  setProfileName(name: string) { this.profileName = name; }

  setSession(sessionKey: string, sessionId: string) {
    this.sessionKey = sessionKey;
    this.sessionId = isUUID(sessionId) ? sessionId : sessionIdFromKey(`${sessionKey}:${sessionId}`);
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      "X-EB-Gateway-ID": this.gatewayId,
      "X-EB-Session-Key": this.sessionKey,
    };
    if (this.agentKey) h["X-EB-Agent-Key"] = this.agentKey;
    if (this.actorId) h["X-EB-Actor-Id"] = this.actorId;
    const authToken = (process.env.EB_AUTH_TOKEN || "").trim();
    if (authToken) h["X-EB-Auth-Token"] = authToken;
    return h;
  }

  private async req<T>(
    method: string,
    path: string,
    body?: unknown,
    statusOK?: number,
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      method,
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(30000),
    });
    if (statusOK !== undefined && res.status === statusOK) return null as unknown as T;
    if (!res.ok) {
      // For 404/403/409, return null instead of throwing
      if (res.status === 404 || res.status === 403 || res.status === 409) return null as unknown as T;
      throw new Error(`EB ${method} ${path} failed: ${res.status}`);
    }
    const text = await res.text();
    return text ? JSON.parse(text) as T : null as unknown as T;
  }

  async search(
    query: string,
    opts?: { max_results?: number; min_score?: number; auto_recall?: boolean; entity_type?: string },
  ): Promise<EBSearchResult[]> {
    return this.req<EBSearchResult[]>("POST", "/memory/search", {
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

  async searchGlobal(
    query: string,
    opts?: { max_results?: number; min_score?: number; auto_recall?: boolean; session_key?: string; entity_type?: string },
  ): Promise<EBSearchResult[]> {
    return this.req<EBSearchResult[]>("POST", "/memory/search", {
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

  async store(
    text: string,
    opts?: {
      category?: string;
      scope?: string;
      confidence?: number;
      goal_ids?: string[];
      decision_status?: string;
    },
  ): Promise<EBFact | null> {
    return this.req<EBFact | null>("POST", "/memory/store", {
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

  async getById(id: string): Promise<EBFact | null> {
    return this.req<EBFact | null>("GET", `/memory/${id}`, undefined, 404);
  }

  async forget(id: string): Promise<boolean> {
    try {
      await this.req<void>("DELETE", `/memory/${id}`);
      return true;
    } catch {
      return false;
    }
  }

  async update(id: string, updates: Record<string, unknown>): Promise<EBFact | null> {
    return this.req<EBFact | null>("PATCH", `/memory/${id}`, updates, 404);
  }

  async inspectActor(
    actorId: string,
    opts?: { include_relationships?: boolean; include_authority_chain?: boolean },
  ): Promise<unknown> {
    const actor = await this.req<unknown>("GET", `/actors/${encodeURIComponent(actorId)}`, undefined, 404);
    const result: Record<string, unknown> = { actor };
    if (opts?.include_relationships) {
      result.relationships = await this.req<unknown>(
        "GET",
        `/actors/${encodeURIComponent(actorId)}/relationships`,
        undefined,
        404,
      );
    }
    if (opts?.include_authority_chain) {
      result.authority_chain = await this.req<unknown>(
        "GET",
        `/actors/${encodeURIComponent(actorId)}/authority-chain`,
        undefined,
        404,
      );
    }
    return result;
  }

  async getClaim(claimId: string): Promise<unknown> {
    return this.req<unknown>("GET", `/claims/${encodeURIComponent(claimId)}`, undefined, 404);
  }

  async lookupProcedureAudit(args: { action_id?: string; lineage_ref?: string }): Promise<unknown> {
    if (args.action_id) {
      return this.req<unknown>("GET", `/procedures/audit/action/${encodeURIComponent(args.action_id)}`);
    }
    const params = new URLSearchParams({ lineage_ref: args.lineage_ref ?? "" });
    return this.req<unknown>("GET", `/procedures/audit/lineage?${params}`);
  }

  async sessionStart(): Promise<void> {
    await this.req<void>("POST", "/sessions/start", {
      session_key: this.sessionKey,
      session_id: this.sessionId,
      gateway_id: this.gatewayId,
      agent_id: this.agentId,
      agent_key: this.agentKey,
      gateway_short_name: this.gatewayShortName,
    });
  }

  async sessionEnd(): Promise<void> {
    await this.req<void>("POST", "/sessions/end", {
      session_key: this.sessionKey,
      session_id: this.sessionId,
      gateway_id: this.gatewayId,
      agent_key: this.agentKey,
    });
  }

  async ingestTurn(messages: Array<{ role: string; content: string }>): Promise<EBIngestTurnResult | null> {
    try {
      return await this.req<EBIngestTurnResult>("POST", "/memory/ingest-turn", {
        session_key: this.sessionKey,
        session_id: this.sessionId,
        profile_name: this.profileName || "coding",
        messages,
      });
    } catch {
      return null;
    }
  }

  async ingestMessages(messages: Array<{ role: string; content: string }>): Promise<boolean> {
    try {
      await this.req<void>("POST", "/memory/ingest-messages", {
        session_key: this.sessionKey,
        session_id: this.sessionId,
        profile_name: this.profileName || "coding",
        messages,
      });
      return true;
    } catch {
      return false;
    }
  }
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export const ElephantBrokerMemory: Plugin = async () => {
  const baseUrl = process.env.EB_RUNTIME_URL ?? "http://localhost:8420";
  const gatewayId = process.env.EB_GATEWAY_ID ?? "";
  const profileName = process.env.EB_PROFILE ?? "coding";
  const agentId = process.env.EB_AGENT_NAME ?? process.env.COGNEE_AGENT_NAME ?? "";
  const agentKey = process.env.EB_AGENT_KEY ?? agentId;
  const actorId = process.env.EB_ACTOR_ID ?? "";
  const gatewayShortName = process.env.EB_GATEWAY_SHORT_NAME ?? "";

  if (!gatewayId) {
    console.warn("[EB] EB_GATEWAY_ID not set — plugin loaded but inactive");
  }

  const client = new EBClient(baseUrl, gatewayId, profileName, agentKey, agentId, actorId, gatewayShortName);

  // ---------------------------------------------------------------------------
  // Conversation auto-capture buffer
  // ---------------------------------------------------------------------------
  const messageBuffer: BufferedMessage[] = [];
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let flushRunning = false;

  function scheduleFlush(delayMs = 2000): void {
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = setTimeout(async () => {
      flushTimer = null;
      await flushMessages();
    }, delayMs);
  }

  async function flushMessages(): Promise<void> {
    if (flushRunning) return;
    flushRunning = true;
    try {
      while (messageBuffer.length > 0) {
        const batch = messageBuffer.splice(0);
        const messages = batch.map((msg) => ({ role: msg.role, content: msg.text }));
        const ingested = await client.ingestTurn(messages);
        if (ingested) continue;

        for (const msg of batch) {
          try {
            await client.store(
              `[${msg.role}] ${msg.text}`,
              { category: "conversation", scope: "session" },
            );
          } catch {
            const stored = await client.ingestMessages([{ role: msg.role, content: msg.text }]);
            if (!stored) console.error("[EB] message capture failed");
          }
        }
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      console.error("[EB] flush failed:", message);
    } finally {
      flushRunning = false;
    }
  }

  const seenMessages = new Set<string>();
  const assistantTexts = new Map<string, string[]>();

  return {
    tool: {
      memory_search: tool({
        description: "Search ElephantBroker memory for relevant facts and context. Returns knowledge from current and past sessions.",
        args: {
          query: tool.schema.string().describe("Natural language search query"),
          max_results: tool.schema.number().optional().default(5)
            .describe("Maximum results to return (1-20)"),
          min_score: tool.schema.number().optional().default(0)
            .describe("Minimum relevance score (0-1)"),
          scope: tool.schema.string().optional()
            .describe("Scope filter (global, session, actor)"),
          entity_type: tool.schema.string().optional()
            .describe("Entity type filter: Product, Supplier, MarketSignal, ResearchDecision, Prospect, CustomsRecord, Deal, FinancialReport, Invoice, Contract, Document"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured. Set EB_GATEWAY_ID and EB_RUNTIME_URL env vars.";
          try {
            const results = await client.search(args.query, {
              max_results: Math.min(args.max_results ?? 5, 20),
              min_score: args.min_score ?? 0,
              entity_type: args.entity_type,
            });
            if (!results || results.length === 0) return "No relevant memories found.";
            return [
              `Found ${results.length} memory result(s):`,
              "",
              ...results.map((r, i) =>
                `[${i + 1}] [${r.category}] ${r.text} (confidence: ${r.confidence.toFixed(2)}, score: ${r.score.toFixed(3)})`
              ),
            ].join("\n");
          } catch (e: unknown) {
            return `Memory search failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      memory_search_global: tool({
        description: "Search the global ElephantBroker knowledge base. Use this for data imported from scrapling, doc-ingestor, or other non-session pipelines.",
        args: {
          query: tool.schema.string().describe("Natural language search query"),
          max_results: tool.schema.number().optional().default(20)
            .describe("Maximum global results to return (1-30)"),
          min_score: tool.schema.number().optional().default(0)
            .describe("Minimum relevance score (0-1)"),
          session_key: tool.schema.string().optional()
            .describe("Optional global session key filter, e.g. scrapling:example-com or doc-ingestor:0-inbox"),
          category: tool.schema.string().optional()
            .describe("Optional category filter, e.g. 'project' for doc-ingestor, 'finance' for financial data"),
          entity_type: tool.schema.string().optional()
            .describe("Optional entity type filter: FinancialReport, Invoice, Contract, Document"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured. Set EB_GATEWAY_ID and EB_RUNTIME_URL env vars.";
          try {
            const results = await client.searchGlobal(args.query, {
              max_results: Math.min(args.max_results ?? 20, 30),
              min_score: args.min_score ?? 0,
              session_key: args.session_key,
              entity_type: args.entity_type,
            });
            if (!results || results.length === 0) return "No relevant global memories found.";
            // Apply category filter if specified (client-side since EB doesn't support it natively)
            const filtered = args.category
              ? results.filter((r) => r.category === args.category)
              : results;
            if (filtered.length === 0) return `No global memories found matching category '${args.category}'.`;
            return [
              `Found ${filtered.length} global memory result(s)${args.category ? ` (category: ${args.category})` : ""}:`,
              "",
              ...filtered.map((r, i) =>
                `[${i + 1}] [${r.category}] ${r.text} (confidence: ${r.confidence.toFixed(2)}, score: ${r.score.toFixed(3)})`
              ),
            ].join("\n");
          } catch (e: unknown) {
            return `Global memory search failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      memory_store: tool({
        description: "Store a fact or piece of knowledge into ElephantBroker memory for future recall.",
        args: {
          text: tool.schema.string().describe("The fact or knowledge to store"),
          category: tool.schema.string().optional().default("general")
            .describe("Category tag (e.g. 'code', 'architecture', 'user-preference')"),
          scope: tool.schema.string().optional().default("session")
            .describe("Visibility scope: session, actor, team, or global"),
          goal_ids: tool.schema.array(tool.schema.string()).optional()
            .describe("Optional fact IDs this fact relates to"),
          decision_status: tool.schema.string().optional()
            .describe("Decision status: proposed, approved, rejected, actioned"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const result = await client.store(args.text, {
              category: args.category,
              scope: args.scope,
              goal_ids: args.goal_ids,
              decision_status: args.decision_status,
            });
            if (!result) return "Memory stored (deduplicated — similar fact already exists).";
            return `Stored memory "${args.text.substring(0, 60)}..." (id: ${result.id})`;
          } catch (e: unknown) {
            return `Memory store failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),
      
      memory_decide: tool({
        description: "Record a decision or finding linked to source facts. Use when you discover an anomaly, make a judgment, or need to track an action based on retrieved data.",
        args: {
          text: tool.schema.string().describe("The decision, finding, or action description"),
          decision_status: tool.schema.string().optional().default("proposed")
            .describe("Decision status: proposed, approved, rejected, actioned"),
          source_fact_ids: tool.schema.array(tool.schema.string()).optional()
            .describe("Source fact IDs this decision is based on"),
          scope: tool.schema.string().optional().default("team"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const result = await client.store(args.text, {
              category: "decision",
              scope: args.scope,
              decision_status: args.decision_status,
              goal_ids: args.source_fact_ids,
            });
            if (!result) return "Decision stored (deduplicated).";
            return `Decision recorded: "${args.text.substring(0, 60)}..." (status: ${args.decision_status}, id: ${result.id})`;
          } catch (e: unknown) {
            return `Decision store failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      memory_get: tool({
        description: "Retrieve a specific memory fact by its ID from ElephantBroker.",
        args: {
          id: tool.schema.string().describe("Fact ID to retrieve"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const fact = await client.getById(args.id);
            if (!fact) return `Fact not found: ${args.id}`;
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
          } catch (e: unknown) {
            return `Memory retrieve failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      memory_forget: tool({
        description: "Delete a memory fact from ElephantBroker by its ID.",
        args: {
          id: tool.schema.string().describe("Fact ID to delete"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const ok = await client.forget(args.id);
            return ok ? `Memory ${args.id} deleted.` : `Failed to delete memory ${args.id} (not found or permission denied).`;
          } catch (e: unknown) {
            return `Memory delete failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      memory_update: tool({
        description: "Update an existing memory fact's text or properties in ElephantBroker.",
        args: {
          id: tool.schema.string().describe("Fact ID to update"),
          text: tool.schema.string().optional().describe("New text content"),
          confidence: tool.schema.number().optional().describe("New confidence value (0-1)"),
          category: tool.schema.string().optional().describe("New category"),
          decision_status: tool.schema.string().optional()
            .describe("Decision status: proposed, approved, rejected, actioned"),
          entity_type: tool.schema.string().optional()
            .describe("Entity type: FinancialReport, Invoice, Contract, Document"),
          goal_ids: tool.schema.array(tool.schema.string()).optional()
            .describe("New goal IDs this fact relates to"),
          archived: tool.schema.boolean().optional()
            .describe("Archive/unarchive this fact"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          const updates: Record<string, unknown> = {};
          if (args.text !== undefined) updates.text = args.text;
          if (args.confidence !== undefined) updates.confidence = args.confidence;
          if (args.category !== undefined) updates.category = args.category;
          if (args.decision_status !== undefined) updates.decision_status = args.decision_status;
          if (args.entity_type !== undefined) updates.entity_type = args.entity_type;
          if (args.goal_ids !== undefined) updates.goal_ids = args.goal_ids;
          if (args.archived !== undefined) updates.archived = args.archived;
          if (Object.keys(updates).length === 0) return "Nothing to update.";
          try {
            const result = await client.update(args.id, updates);
            if (!result) return `Fact not found: ${args.id}`;
            return `Memory ${args.id} updated successfully.`;
          } catch (e: unknown) {
            return `Memory update failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      actor_inspect: tool({
        description: "Read actor details with optional relationships and authority-chain context.",
        args: {
          actor_id: tool.schema.string().describe("Actor UUID to inspect"),
          include_relationships: tool.schema.boolean().optional().default(false)
            .describe("Include actor relationship records"),
          include_authority_chain: tool.schema.boolean().optional().default(false)
            .describe("Include actor authority-chain records"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const result = await client.inspectActor(args.actor_id, {
              include_relationships: args.include_relationships,
              include_authority_chain: args.include_authority_chain,
            });
            return JSON.stringify(result, null, 2);
          } catch (e: unknown) {
            return `Actor inspect failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      claim_get: tool({
        description: "Read a claim and its current verification state by claim ID.",
        args: {
          claim_id: tool.schema.string().describe("Claim UUID to read"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          try {
            const result = await client.getClaim(args.claim_id);
            return JSON.stringify(result, null, 2);
          } catch (e: unknown) {
            return `Claim get failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),

      procedure_audit_lookup: tool({
        description: "Read procedure audit events by action ID or lineage reference.",
        args: {
          action_id: tool.schema.string().optional().describe("Action ID to look up"),
          lineage_ref: tool.schema.string().optional().describe("Lineage reference to look up"),
        },
        async execute(args) {
          if (!gatewayId) return "EB_GATEWAY_ID not configured.";
          if (Boolean(args.action_id) === Boolean(args.lineage_ref)) {
            return "Provide exactly one of action_id or lineage_ref.";
          }
          try {
            const result = await client.lookupProcedureAudit({
              action_id: args.action_id,
              lineage_ref: args.lineage_ref,
            });
            return JSON.stringify(result, null, 2);
          } catch (e: unknown) {
            return `Procedure audit lookup failed: ${e instanceof Error ? e.message : String(e)}`;
          }
        },
      }),
    },

    "chat.message": async (_input, output) => {
      if (!gatewayId) return;
      const msgId = _input.messageID ?? output.message.id;
      if (!msgId || seenMessages.has(msgId)) return;
      seenMessages.add(msgId);

      const textParts = (output.parts as unknown[]).filter(isTextMessagePart);
      const content = textParts.map((p) => p.text).join("\n").trim();
      if (!content) return;

      messageBuffer.push({
        role: "user",
        text: content,
        timestamp: Date.now(),
      });
      scheduleFlush();
    },

    event: async ({ event }: { event: unknown }) => {
      if (!gatewayId) return;
      const ev = event as unknown as { type: string; properties?: Record<string, unknown> };

      if (ev.type === "session.created") {
        const sessionID = ev.properties?.sessionID;
        if (sessionID) {
          client.setSession(`opencode:${sessionID}`, String(sessionID));
        }
        client.sessionStart().catch((e: unknown) => console.error("[EB] sessionStart failed:", e instanceof Error ? e.message : String(e)));
      }

      if (ev.type === "session.idle" || ev.type === "session.deleted") {
        await flushMessages();
        client.sessionEnd().catch((e: unknown) => console.error("[EB] sessionEnd failed:", e instanceof Error ? e.message : String(e)));
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

export default ElephantBrokerMemory;
