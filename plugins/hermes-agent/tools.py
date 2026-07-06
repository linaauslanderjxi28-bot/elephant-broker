from __future__ import annotations

import json
from typing import Any, Protocol


class ToolProvider(Protocol):
    @property
    def name(self) -> str: ...

    _session_key: str
    _session_id: str
    _profile_name: str

    def _eb_request(self, path: str, payload: dict[str, Any] | None = None, *, method: str = "POST", timeout: float = 30.0) -> Any: ...


def handle_tool_call(provider: ToolProvider, tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "elephantbroker_search":
        return handle_search(provider, args)
    if tool_name == "elephantbroker_search_global":
        return handle_search_global(provider, args)
    if tool_name == "elephantbroker_store":
        return handle_store(provider, args)
    raise NotImplementedError(f"Provider {provider.name} does not handle tool {tool_name}")


def handle_search(provider: ToolProvider, args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})
    payload = {
        "query": query,
        "max_results": min(int(args.get("max_results", 5)), 20),
    }
    scope = args.get("scope")
    if scope:
        if scope not in VALID_SCOPES:
            return json.dumps({"error": f"Invalid scope '{scope}'. Must be one of: {', '.join(sorted(VALID_SCOPES))}"})
        payload["scope"] = scope
    # When scope is omitted, the backend returns results from ALL scopes.
    # Only pass session_key/session_id for non-global searches to avoid
    # premature session filtering at the orchestrator level.
    if scope != "global":
        payload["session_key"] = provider._session_key
        payload["session_id"] = provider._session_id
    payload["auto_recall"] = True
    if args.get("entity_type"):
        payload["entity_type"] = args["entity_type"]
    try:
        results = provider._eb_request("/memory/search", payload, timeout=10.0)
        if not results:
            return json.dumps({"result": "No matching memories found."})
        return json.dumps({"results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"error": f"Search failed: {e}"})


def handle_search_global(provider: ToolProvider, args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})
    payload = {"query": query, "max_results": min(int(args.get("max_results", 10)), 20), "scope": "global"}
    for key in ("session_key", "entity_type"):
        if args.get(key):
            payload[key] = args[key]
    if provider._profile_name:
        payload["profile_name"] = provider._profile_name
    try:
        results = provider._eb_request("/memory/search", payload, timeout=15.0)
        if not results:
            return json.dumps({"result": "No matching global memories found."})
        return json.dumps({"results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"error": f"Global search failed: {e}"})


VALID_SCOPES = {"session", "actor", "team", "organization", "global", "task", "subagent", "artifact"}

def handle_store(provider: ToolProvider, args: dict[str, Any]) -> str:
    text = args.get("text", "")
    if not text:
        return json.dumps({"error": "Missing required parameter: text"})
    scope = args.get("scope", "session")
    if scope not in VALID_SCOPES:
        return json.dumps({"error": f"Invalid scope '{scope}'. Must be one of: {', '.join(sorted(VALID_SCOPES))}"})
    fact = {
        "text": text,
        "category": args.get("category", "general"),
        "scope": scope,
        "memory_class": "episodic",
        "confidence": 1.0,
    }
    for key in ("entity_type", "decision_status"):
        if args.get(key):
            fact[key] = args[key]
    payload = {"fact": fact, "session_key": provider._session_key, "session_id": provider._session_id}
    if args.get("goal_ids"):
        payload["goal_ids"] = args["goal_ids"]
    try:
        res = provider._eb_request("/memory/store", payload, timeout=10.0)
        return json.dumps({"result": "Fact stored successfully.", "details": res})
    except Exception as e:
        return json.dumps({"error": f"Failed to store fact: {e}"})
