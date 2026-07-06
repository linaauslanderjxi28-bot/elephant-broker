SEARCH_SCHEMA = {
    "name": "elephantbroker_search",
    "description": "Search ElephantBroker long-term memories by semantic meaning. Returns relevant facts, user preferences, and prior conversation QA.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "Max results to return (default: 5, max: 20)."},
            "entity_type": {"type": "string", "description": "Entity type filter: FinancialReport, Invoice, Contract, Document"},
        },
        "required": ["query"],
    },
}

SEARCH_GLOBAL_SCHEMA = {
    "name": "elephantbroker_search_global",
    "description": "Search the global ElephantBroker knowledge base. Use this for data imported from scrapling, doc-ingestor, or other non-session pipelines.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The global search query."},
            "max_results": {"type": "integer", "description": "Max global results to return (default: 10, max: 20)."},
            "session_key": {"type": "string", "description": "Optional global session key filter, e.g. scrapling:example-com or doc-ingestor:0-inbox."},
            "entity_type": {"type": "string", "description": "Entity type filter: FinancialReport, Invoice, Contract, Document"},
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "elephantbroker_store",
    "description": "Store a durable, explicit fact in ElephantBroker memory. Use this to persist corrections, key user decisions, or lasting preferences.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact text to store."},
            "category": {"type": "string", "description": "Optional category label (default: 'general')."},
            "entity_type": {"type": "string", "description": "Entity type: FinancialReport, Invoice, Contract, Document"},
            "decision_status": {"type": "string", "description": "Decision status: proposed, approved, rejected, actioned"},
            "goal_ids": {"type": "array", "items": {"type": "string"}, "description": "Fact IDs this fact relates to"},
        },
        "required": ["text"],
    },
}
