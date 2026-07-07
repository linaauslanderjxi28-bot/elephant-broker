SCOPE_ENUM = ["session", "actor", "team", "organization", "global", "task", "subagent", "artifact"]
MEMORY_CLASS_ENUM = ["episodic", "semantic", "procedural"]

SEARCH_SCHEMA = {
    "name": "elephantbroker_search",
    "description": "Search ElephantBroker memories across scopes by semantic meaning. Omit scope to search all accessible scopes.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "Max results to return (default: 5, max: 20)."},
            "min_score": {"type": "number", "description": "Minimum similarity score (default: 0.0)."},
            "scope": {"type": "string", "description": "Optional scope filter.", "enum": SCOPE_ENUM},
            "entity_type": {"type": "string", "description": "Optional entity type filter, e.g. Document or ResearchDecision."},
            "memory_class": {"type": "string", "description": "Optional memory class filter.", "enum": MEMORY_CLASS_ENUM},
            "session_key": {"type": "string", "description": "Optional session key filter. Session scope defaults to the current Hermes session."},
            "profile_name": {"type": "string", "description": "Optional retrieval profile override."},
            "include_audit": {"type": "boolean", "description": "Include tool-call/conversation/todowrite audit records. Defaults to false."},
        },
        "required": ["query"],
    },
}

SEARCH_GLOBAL_SCHEMA = {
    "name": "elephantbroker_search_global",
    "description": "Search only global-scope memories imported by external pipelines or shared globally.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The global search query."},
            "max_results": {"type": "integer", "description": "Max global results to return (default: 10, max: 20)."},
            "min_score": {"type": "number", "description": "Minimum similarity score (default: 0.0)."},
            "session_key": {"type": "string", "description": "Optional global session key filter, e.g. doc-ingestor:0-inbox."},
            "entity_type": {"type": "string", "description": "Optional entity type filter."},
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "elephantbroker_store",
    "description": "Store a durable, explicit fact in ElephantBroker memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact text to store."},
            "category": {"type": "string", "description": "Optional category label (default: general)."},
            "scope": {"type": "string", "description": "Visibility scope (default: session).", "enum": SCOPE_ENUM},
            "memory_class": {"type": "string", "description": "Memory class (default: episodic).", "enum": MEMORY_CLASS_ENUM},
            "confidence": {"type": "number", "description": "Confidence from 0.0 to 1.0 (default: 1.0)."},
            "entity_type": {"type": "string", "description": "Optional entity type, e.g. Document or ResearchDecision."},
            "entity_name": {"type": "string", "description": "Optional entity display name for typed facts."},
            "decision_status": {"type": "string", "description": "Decision status: proposed, approved, rejected, actioned."},
            "decision_domain": {"type": "string", "description": "Optional decision domain tag."},
            "target_actor_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional actor UUIDs this fact targets."},
            "goal_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional related goal UUIDs. Values must be UUIDs."},
            "autorecall_blacklisted": {"type": "boolean", "description": "Exclude this fact from auto-recall."},
        },
        "required": ["text"],
    },
}

GET_SCHEMA = {
    "name": "elephantbroker_get",
    "description": "Read a specific ElephantBroker memory fact by ID.",
    "parameters": {"type": "object", "properties": {"fact_id": {"type": "string"}}, "required": ["fact_id"]},
}

UPDATE_SCHEMA = {
    "name": "elephantbroker_update",
    "description": "Update allowed fields on an existing ElephantBroker memory fact by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "fact_id": {"type": "string"},
            "text": {"type": "string"},
            "category": {"type": "string"},
            "scope": {"type": "string", "enum": SCOPE_ENUM},
            "confidence": {"type": "number"},
            "memory_class": {"type": "string", "enum": MEMORY_CLASS_ENUM},
            "decision_domain": {"type": "string"},
            "archived": {"type": "boolean"},
            "autorecall_blacklisted": {"type": "boolean"},
            "target_actor_ids": {"type": "array", "items": {"type": "string"}},
            "goal_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fact_id"],
    },
}

FORGET_SCHEMA = {
    "name": "elephantbroker_forget",
    "description": "Delete a memory fact by ID. Use only for explicit cleanup or incorrect facts.",
    "parameters": {"type": "object", "properties": {"fact_id": {"type": "string"}}, "required": ["fact_id"]},
}

SESSION_GOALS_LIST_SCHEMA = {
    "name": "elephantbroker_session_goals_list",
    "description": "List the current Hermes session goal tree with statuses, blockers, and IDs.",
    "parameters": {"type": "object", "properties": {}},
}

GOAL_CREATE_SCHEMA = {
    "name": "elephantbroker_goal_create",
    "description": "Create a session-scoped goal or sub-goal. Call session_goals_list first to avoid duplicates.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "parent_goal_id": {"type": "string"},
            "success_criteria": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    },
}

GOAL_UPDATE_STATUS_SCHEMA = {
    "name": "elephantbroker_goal_update_status",
    "description": "Update a session goal status with optional evidence.",
    "parameters": {
        "type": "object",
        "properties": {"goal_id": {"type": "string"}, "status": {"type": "string"}, "evidence": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["goal_id", "status"],
    },
}

GOAL_ADD_BLOCKER_SCHEMA = {
    "name": "elephantbroker_goal_add_blocker",
    "description": "Record a blocker on a session goal.",
    "parameters": {"type": "object", "properties": {"goal_id": {"type": "string"}, "blocker": {"type": "string"}}, "required": ["goal_id", "blocker"]},
}

GOAL_PROGRESS_SCHEMA = {
    "name": "elephantbroker_goal_progress",
    "description": "Record evidence of progress on a session goal.",
    "parameters": {"type": "object", "properties": {"goal_id": {"type": "string"}, "evidence": {"type": "string"}}, "required": ["goal_id", "evidence"]},
}

PROCEDURE_CREATE_SCHEMA = {
    "name": "elephantbroker_procedure_create",
    "description": "Create a reusable procedure with ordered steps.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "scope": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object"}},
            "enabled": {"type": "boolean"},
            "is_manual_only": {"type": "boolean"},
            "activation_modes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "steps"],
    },
}

PROCEDURE_ACTIVATE_SCHEMA = {
    "name": "elephantbroker_procedure_activate",
    "description": "Activate a procedure for the current Hermes session.",
    "parameters": {"type": "object", "properties": {"procedure_id": {"type": "string"}, "actor_id": {"type": "string"}}, "required": ["procedure_id"]},
}

PROCEDURE_COMPLETE_STEP_SCHEMA = {
    "name": "elephantbroker_procedure_complete_step",
    "description": "Mark a procedure execution step complete with optional proof value.",
    "parameters": {
        "type": "object",
        "properties": {"execution_id": {"type": "string"}, "step_id": {"type": "string"}, "proof_value": {"type": "string"}, "lineage_refs": {"type": "array", "items": {"type": "string"}}},
        "required": ["execution_id", "step_id"],
    },
}

PROCEDURE_STATUS_SCHEMA = {
    "name": "elephantbroker_procedure_status",
    "description": "Read procedure execution status for the current Hermes session.",
    "parameters": {"type": "object", "properties": {}},
}

PROCEDURE_AUDIT_LOOKUP_SCHEMA = {
    "name": "elephantbroker_procedure_audit_lookup",
    "description": "Look up procedure audit events by action_id or lineage_ref.",
    "parameters": {"type": "object", "properties": {"action_id": {"type": "string"}, "lineage_ref": {"type": "string"}}},
}

ARTIFACT_SEARCH_SCHEMA = {
    "name": "elephantbroker_artifact_search",
    "description": "Search session or persistent tool artifacts by query.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "tool_name": {"type": "string"}, "scope": {"type": "string", "enum": ["session", "persistent", "all"]}, "max_results": {"type": "integer"}},
        "required": ["query"],
    },
}

ARTIFACT_CREATE_SCHEMA = {
    "name": "elephantbroker_artifact_create",
    "description": "Create a session or persistent tool artifact for evidence, command output, or reports.",
    "parameters": {
        "type": "object",
        "properties": {"tool_name": {"type": "string"}, "content": {"type": "string"}, "summary": {"type": "string"}, "scope": {"type": "string", "enum": ["session", "persistent"]}, "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["tool_name", "content"],
    },
}

ACTOR_INSPECT_SCHEMA = {
    "name": "elephantbroker_actor_inspect",
    "description": "Read actor details with optional relationships and authority-chain context.",
    "parameters": {"type": "object", "properties": {"actor_id": {"type": "string"}, "include_relationships": {"type": "boolean"}, "include_authority_chain": {"type": "boolean"}}, "required": ["actor_id"]},
}

CLAIM_GET_SCHEMA = {
    "name": "elephantbroker_claim_get",
    "description": "Read a claim and its current verification state by claim ID.",
    "parameters": {"type": "object", "properties": {"claim_id": {"type": "string"}}, "required": ["claim_id"]},
}

GUARDS_LIST_SCHEMA = {
    "name": "elephantbroker_guards_list",
    "description": "List active guard rules, pending approvals, and recent guard events for the current session.",
    "parameters": {"type": "object", "properties": {}},
}

GUARD_STATUS_SCHEMA = {
    "name": "elephantbroker_guard_status",
    "description": "Read details for a guard event in the current session.",
    "parameters": {"type": "object", "properties": {"guard_event_id": {"type": "string"}}, "required": ["guard_event_id"]},
}

ALL_SCHEMAS = [
    SEARCH_SCHEMA,
    SEARCH_GLOBAL_SCHEMA,
    STORE_SCHEMA,
    GET_SCHEMA,
    UPDATE_SCHEMA,
    FORGET_SCHEMA,
    SESSION_GOALS_LIST_SCHEMA,
    GOAL_CREATE_SCHEMA,
    GOAL_UPDATE_STATUS_SCHEMA,
    GOAL_ADD_BLOCKER_SCHEMA,
    GOAL_PROGRESS_SCHEMA,
    PROCEDURE_CREATE_SCHEMA,
    PROCEDURE_ACTIVATE_SCHEMA,
    PROCEDURE_COMPLETE_STEP_SCHEMA,
    PROCEDURE_STATUS_SCHEMA,
    PROCEDURE_AUDIT_LOOKUP_SCHEMA,
    ARTIFACT_SEARCH_SCHEMA,
    ARTIFACT_CREATE_SCHEMA,
    ACTOR_INSPECT_SCHEMA,
    CLAIM_GET_SCHEMA,
    GUARDS_LIST_SCHEMA,
    GUARD_STATUS_SCHEMA,
]
