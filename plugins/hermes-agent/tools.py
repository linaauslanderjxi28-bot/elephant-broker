from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.parse import urlencode


AUDIT_CATEGORIES = {"tool-call", "conversation", "todowrite"}
VALID_SCOPES = {"session", "actor", "team", "organization", "global", "task", "subagent", "artifact"}
VALID_MEMORY_CLASSES = {"episodic", "semantic", "procedural"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
FACT_UPDATE_FIELDS = {
    "text",
    "category",
    "scope",
    "confidence",
    "memory_class",
    "target_actor_ids",
    "goal_ids",
    "decision_domain",
    "archived",
    "autorecall_blacklisted",
}


class ToolProvider(Protocol):
    @property
    def name(self) -> str: ...

    _session_key: str
    _session_id: str
    _profile_name: str

    def _eb_request(self, path: str, payload: dict[str, Any] | None = None, *, method: str = "POST", timeout: float = 30.0) -> Any: ...


def _json_result(value: Any) -> str:
    return json.dumps(value)


def _missing(name: str) -> str:
    return _json_result({"error": f"Missing required parameter: {name}"})


def _query(path: str, params: dict[str, Any]) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    if not clean:
        return path
    return f"{path}?{urlencode(clean, doseq=True)}"


def _filter_audit_results(results: list[dict[str, Any]], include_audit: bool) -> list[dict[str, Any]]:
    if include_audit:
        return results
    return [result for result in results if result.get("category") not in AUDIT_CATEGORIES]


def _error_text(exc: OSError) -> str:
    return str(exc)


def _is_timeout(exc: OSError) -> bool:
    return isinstance(exc, TimeoutError) or "timed out" in _error_text(exc).lower() or "timeout" in _error_text(exc).lower()


def _is_http_status(exc: OSError, status: int) -> bool:
    text = _error_text(exc).lower()
    return f"http error {status}" in text or f"{status}" in text


def _optional_unavailable(feature: str, reason: str, detail: str) -> str:
    return _json_result({"status": "unavailable", "feature": feature, "reason": reason, "message": detail})


def _session_params(provider: ToolProvider) -> dict[str, str]:
    return {"session_key": provider._session_key, "session_id": provider._session_id}


def _request_json(provider: ToolProvider, path: str, payload: dict[str, Any] | None = None, *, method: str = "POST", timeout: float = 30.0) -> str:
    try:
        return _json_result(provider._eb_request(path, payload, method=method, timeout=timeout))
    except OSError as exc:
        return _json_result({"error": str(exc)})


def _procedure_steps(raw_steps: Any) -> list[Any]:
    if not isinstance(raw_steps, list):
        return raw_steps
    steps = []
    for index, step in enumerate(raw_steps):
        if isinstance(step, str):
            steps.append({"order": index, "instruction": step})
        elif isinstance(step, dict):
            normalized = dict(step)
            normalized.setdefault("order", index)
            if "instruction" not in normalized and "description" in normalized:
                normalized["instruction"] = normalized["description"]
            steps.append(normalized)
        else:
            steps.append(step)
    return steps


def _procedure_activation_modes(raw_modes: Any) -> list[Any]:
    if not isinstance(raw_modes, list):
        return raw_modes
    modes = []
    for mode in raw_modes:
        if isinstance(mode, str):
            if mode == "manual":
                modes.append({"manual": True})
            elif mode == "actor_default":
                modes.append({"actor_default": True})
            elif mode == "goal_bound":
                modes.append({"goal_bound": True})
            elif mode == "supervisor_forced":
                modes.append({"supervisor_forced": True})
            else:
                modes.append({"trigger_word": mode})
        else:
            modes.append(mode)
    return modes


def _recent_artifacts(provider: ToolProvider) -> list[dict[str, Any]]:
    artifacts = getattr(provider, "_recent_session_artifacts", None)
    if isinstance(artifacts, list):
        return artifacts
    artifacts = []
    setattr(provider, "_recent_session_artifacts", artifacts)
    return artifacts


def _remember_session_artifact(provider: ToolProvider, artifact: dict[str, Any]) -> None:
    artifacts = _recent_artifacts(provider)
    artifacts.insert(0, artifact)
    del artifacts[20:]


def _search_recent_artifacts(provider: ToolProvider, query: str, tool_name: Any, max_results: int) -> list[dict[str, Any]]:
    query_tokens = set(str(query).lower().split())
    if not query_tokens:
        return []
    scored = []
    for artifact in _recent_artifacts(provider):
        if tool_name and artifact.get("tool_name") != tool_name:
            continue
        haystack = f"{artifact.get('summary', '')} {artifact.get('content', '')} {artifact.get('tool_name', '')}"
        artifact_tokens = set(haystack.lower().split())
        overlap = query_tokens & artifact_tokens
        if overlap:
            scored.append((len(overlap), artifact))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [artifact for _score, artifact in scored[:max_results]]


def handle_tool_call(provider: ToolProvider, tool_name: str, args: dict[str, Any]) -> str:
    handlers = {
        "elephantbroker_search": handle_search,
        "elephantbroker_search_global": handle_search_global,
        "elephantbroker_store": handle_store,
        "elephantbroker_get": handle_get,
        "elephantbroker_update": handle_update,
        "elephantbroker_forget": handle_forget,
        "elephantbroker_session_goals_list": handle_session_goals_list,
        "elephantbroker_goal_create": handle_goal_create,
        "elephantbroker_goal_update_status": handle_goal_update_status,
        "elephantbroker_goal_add_blocker": handle_goal_add_blocker,
        "elephantbroker_goal_progress": handle_goal_progress,
        "elephantbroker_procedure_create": handle_procedure_create,
        "elephantbroker_procedure_activate": handle_procedure_activate,
        "elephantbroker_procedure_complete_step": handle_procedure_complete_step,
        "elephantbroker_procedure_status": handle_procedure_status,
        "elephantbroker_procedure_audit_lookup": handle_procedure_audit_lookup,
        "elephantbroker_artifact_search": handle_artifact_search,
        "elephantbroker_artifact_create": handle_artifact_create,
        "elephantbroker_actor_inspect": handle_actor_inspect,
        "elephantbroker_claim_get": handle_claim_get,
        "elephantbroker_guards_list": handle_guards_list,
        "elephantbroker_guard_status": handle_guard_status,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        raise NotImplementedError(f"Provider {provider.name} does not handle tool {tool_name}")
    return handler(provider, args)


def handle_search(provider: ToolProvider, args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return _missing("query")
    scope = args.get("scope")
    if scope and scope not in VALID_SCOPES:
        return _json_result({"error": f"Invalid scope '{scope}'. Must be one of: {', '.join(sorted(VALID_SCOPES))}"})
    memory_class = args.get("memory_class")
    if memory_class and memory_class not in VALID_MEMORY_CLASSES:
        return _json_result({"error": f"Invalid memory_class '{memory_class}'. Must be one of: {', '.join(sorted(VALID_MEMORY_CLASSES))}"})
    payload = {
        "query": query,
        "max_results": min(int(args.get("max_results", 5)), 20),
        "min_score": float(args.get("min_score", 0.0)),
        "include_audit": bool(args.get("include_audit", False)),
        "auto_recall": True,
    }
    for key in ("scope", "entity_type", "memory_class", "session_key", "profile_name"):
        if args.get(key):
            payload[key] = args[key]
    if scope == "session" and not args.get("session_key"):
        payload.update(_session_params(provider))
    try:
        results = provider._eb_request("/memory/search", payload, timeout=10.0)
        if not results:
            return _json_result({"result": "No matching memories found."})
        filtered = _filter_audit_results(results, bool(payload["include_audit"]))
        if not filtered:
            return _json_result({"result": "No matching memories found."})
        return _json_result({"results": filtered, "count": len(filtered)})
    except OSError as exc:
        return _json_result({"error": f"Search failed: {exc}"})


def handle_search_global(provider: ToolProvider, args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return _missing("query")
    payload = {
        "query": query,
        "max_results": min(int(args.get("max_results", 10)), 20),
        "min_score": float(args.get("min_score", 0.0)),
        "scope": "global",
    }
    for key in ("session_key", "entity_type"):
        if args.get(key):
            payload[key] = args[key]
    if provider._profile_name:
        payload["profile_name"] = provider._profile_name
    try:
        results = provider._eb_request("/memory/search", payload, timeout=15.0)
        if not results:
            return _json_result({"result": "No matching global memories found."})
        return _json_result({"results": results, "count": len(results)})
    except OSError as exc:
        if _is_timeout(exc):
            return _json_result({"status": "degraded", "reason": "timeout", "message": "Global search timed out; use elephantbroker_search without global scope or retry later.", "retryable": True})
        return _json_result({"error": f"Global search failed: {exc}"})


def handle_store(provider: ToolProvider, args: dict[str, Any]) -> str:
    text = args.get("text", "")
    if not text:
        return _missing("text")
    scope = args.get("scope", "session")
    if scope not in VALID_SCOPES:
        return _json_result({"error": f"Invalid scope '{scope}'. Must be one of: {', '.join(sorted(VALID_SCOPES))}"})
    memory_class = args.get("memory_class", "episodic")
    if memory_class not in VALID_MEMORY_CLASSES:
        return _json_result({"error": f"Invalid memory_class '{memory_class}'. Must be one of: {', '.join(sorted(VALID_MEMORY_CLASSES))}"})
    fact = {
        "text": text,
        "category": args.get("category", "general"),
        "scope": scope,
        "memory_class": memory_class,
        "confidence": float(args.get("confidence", 1.0)),
    }
    for key in ("entity_type", "entity_name", "decision_status", "decision_domain", "target_actor_ids", "autorecall_blacklisted"):
        if key in args and args[key] not in (None, ""):
            fact[key] = args[key]
    payload = {"fact": fact, "session_key": provider._session_key, "session_id": provider._session_id}
    if args.get("goal_ids"):
        payload["goal_ids"] = args["goal_ids"]
    try:
        res = provider._eb_request("/memory/store", payload, timeout=10.0)
        return _json_result({"result": "Fact stored successfully.", "details": res})
    except OSError as exc:
        if _is_http_status(exc, 503) or _is_timeout(exc):
            return _json_result({"status": "degraded", "reason": "store_unavailable", "message": "Memory store is temporarily unavailable; retry is safe.", "retryable": True, "detail": _error_text(exc)})
        return _json_result({"error": f"Failed to store fact: {exc}"})


def handle_get(provider: ToolProvider, args: dict[str, Any]) -> str:
    fact_id = args.get("fact_id", "")
    if not fact_id:
        return _missing("fact_id")
    return _request_json(provider, f"/memory/{fact_id}", None, method="GET", timeout=10.0)


def handle_update(provider: ToolProvider, args: dict[str, Any]) -> str:
    fact_id = args.get("fact_id", "")
    if not fact_id:
        return _missing("fact_id")
    updates = {key: args[key] for key in FACT_UPDATE_FIELDS if key in args and args[key] is not None}
    if not updates:
        return _json_result({"error": "No update fields provided."})
    return _request_json(provider, f"/memory/{fact_id}", updates, method="PATCH", timeout=10.0)


def handle_forget(provider: ToolProvider, args: dict[str, Any]) -> str:
    fact_id = args.get("fact_id", "")
    if not fact_id:
        return _missing("fact_id")
    return _request_json(provider, f"/memory/{fact_id}", None, method="DELETE", timeout=10.0)


def handle_session_goals_list(provider: ToolProvider, args: dict[str, Any]) -> str:
    return _request_json(provider, _query("/goals/session", _session_params(provider)), None, method="GET", timeout=10.0)


def handle_goal_create(provider: ToolProvider, args: dict[str, Any]) -> str:
    title = args.get("title", "")
    if not title:
        return _missing("title")
    payload = {
        "title": title,
        "description": args.get("description", ""),
        "success_criteria": args.get("success_criteria", []),
    }
    if args.get("parent_goal_id"):
        payload["parent_goal_id"] = args["parent_goal_id"]
    return _request_json(provider, _query("/goals/session", _session_params(provider)), payload, timeout=10.0)


def handle_goal_update_status(provider: ToolProvider, args: dict[str, Any]) -> str:
    goal_id = args.get("goal_id", "")
    if not goal_id:
        return _missing("goal_id")
    status = args.get("status", "")
    if not status:
        return _missing("status")
    payload = {"status": status}
    for key in ("evidence", "confidence"):
        if args.get(key) is not None:
            payload[key] = args[key]
    return _request_json(provider, _query(f"/goals/session/{goal_id}", _session_params(provider)), payload, method="PATCH", timeout=10.0)


def handle_goal_add_blocker(provider: ToolProvider, args: dict[str, Any]) -> str:
    goal_id = args.get("goal_id", "")
    blocker = args.get("blocker", "")
    if not goal_id:
        return _missing("goal_id")
    if not blocker:
        return _missing("blocker")
    return _request_json(provider, _query(f"/goals/session/{goal_id}/blocker", _session_params(provider)), {"blocker": blocker}, timeout=10.0)


def handle_goal_progress(provider: ToolProvider, args: dict[str, Any]) -> str:
    goal_id = args.get("goal_id", "")
    evidence = args.get("evidence", "")
    if not goal_id:
        return _missing("goal_id")
    if not evidence:
        return _missing("evidence")
    return _request_json(provider, _query(f"/goals/session/{goal_id}/progress", _session_params(provider)), {"evidence": evidence}, timeout=10.0)


def handle_procedure_create(provider: ToolProvider, args: dict[str, Any]) -> str:
    name = args.get("name", "")
    if not name:
        return _missing("name")
    steps = args.get("steps")
    if not steps:
        return _missing("steps")
    payload = {
        "name": name,
        "description": args.get("description", ""),
        "scope": args.get("scope", "session"),
        "steps": _procedure_steps(steps),
        "enabled": args.get("enabled", True),
        "is_manual_only": args.get("is_manual_only", not bool(args.get("activation_modes"))),
    }
    if args.get("activation_modes"):
        payload["activation_modes"] = _procedure_activation_modes(args["activation_modes"])
    return _request_json(provider, "/procedures/", payload, timeout=10.0)


def handle_procedure_activate(provider: ToolProvider, args: dict[str, Any]) -> str:
    procedure_id = args.get("procedure_id", "")
    if not procedure_id:
        return _missing("procedure_id")
    payload = {"session_key": provider._session_key, "session_id": provider._session_id, "profile_name": provider._profile_name}
    if args.get("actor_id"):
        payload["actor_id"] = args["actor_id"]
    return _request_json(provider, f"/procedures/{procedure_id}/activate", payload, timeout=10.0)


def handle_procedure_complete_step(provider: ToolProvider, args: dict[str, Any]) -> str:
    execution_id = args.get("execution_id", "")
    step_id = args.get("step_id", "")
    if not execution_id:
        return _missing("execution_id")
    if not step_id:
        return _missing("step_id")
    payload = {}
    for key in ("proof_value", "lineage_refs"):
        if args.get(key) is not None:
            payload[key] = args[key]
    return _request_json(provider, f"/procedures/{execution_id}/step/{step_id}/complete", payload, timeout=10.0)


def handle_procedure_status(provider: ToolProvider, args: dict[str, Any]) -> str:
    return _request_json(provider, _query("/procedures/session/status", _session_params(provider)), None, method="GET", timeout=10.0)


def handle_procedure_audit_lookup(provider: ToolProvider, args: dict[str, Any]) -> str:
    if args.get("action_id"):
        return _request_json(provider, f"/procedures/audit/action/{args['action_id']}", None, method="GET", timeout=10.0)
    if args.get("lineage_ref"):
        return _request_json(provider, _query("/procedures/audit/lineage", {"lineage_ref": args["lineage_ref"]}), None, method="GET", timeout=10.0)
    return _json_result({"error": "Provide action_id or lineage_ref."})


def handle_artifact_search(provider: ToolProvider, args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return _missing("query")
    scope = args.get("scope", "all")
    max_results = min(int(args.get("max_results", 5)), 50)
    results = {}
    if scope in ("session", "all"):
        if UUID_RE.match(str(query)):
            path = _query(f"/artifacts/session/{query}", _session_params(provider))
            results["session"] = provider._eb_request(path, None, method="GET", timeout=10.0)
        else:
            payload = {"query": query, "tool_name": args.get("tool_name"), "max_results": max_results, **_session_params(provider)}
            session_results = provider._eb_request("/artifacts/session/search", payload, timeout=10.0)
            if not session_results:
                session_results = _search_recent_artifacts(provider, str(query), args.get("tool_name"), max_results)
            results["session"] = session_results
    if scope in ("persistent", "all"):
        payload = {"query": query, "tool_name": args.get("tool_name"), "max_results": max_results}
        results["persistent"] = provider._eb_request("/artifacts/search", payload, timeout=10.0)
    return _json_result(results)


def handle_artifact_create(provider: ToolProvider, args: dict[str, Any]) -> str:
    tool_name = args.get("tool_name", "")
    content = args.get("content", "")
    if not tool_name:
        return _missing("tool_name")
    if not content:
        return _missing("content")
    payload = {
        "tool_name": tool_name,
        "content": content,
        "summary": args.get("summary") or content[:200],
        "scope": args.get("scope", "session"),
        "tags": args.get("tags", []),
        **_session_params(provider),
    }
    output = _request_json(provider, "/artifacts/create", payload, timeout=10.0)
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(parsed, dict) and "error" not in parsed and payload["scope"] == "session":
        artifact = {**payload, **parsed}
        _remember_session_artifact(provider, artifact)
    return output


def handle_actor_inspect(provider: ToolProvider, args: dict[str, Any]) -> str:
    actor_id = args.get("actor_id", "")
    if not actor_id:
        return _missing("actor_id")
    if not UUID_RE.match(str(actor_id)):
        return _optional_unavailable("actor_inspect", "invalid_actor_id", "Actor inspect requires a UUID actor_id; this deployment rejected the provided value before calling the backend.")
    try:
        result = {"actor": provider._eb_request(f"/actors/{actor_id}", None, method="GET", timeout=10.0)}
        if args.get("include_relationships"):
            result["relationships"] = provider._eb_request(f"/actors/{actor_id}/relationships", None, method="GET", timeout=10.0)
        if args.get("include_authority_chain"):
            result["authority_chain"] = provider._eb_request(f"/actors/{actor_id}/authority-chain", None, method="GET", timeout=10.0)
        return _json_result(result)
    except OSError as exc:
        if _is_http_status(exc, 404) or _is_http_status(exc, 422):
            return _optional_unavailable("actor_inspect", "actor_module_unavailable", f"Actor inspection is unavailable or actor_id is not registered in this deployment: {_error_text(exc)}")
        return _json_result({"error": f"Actor inspect failed: {exc}"})


def handle_claim_get(provider: ToolProvider, args: dict[str, Any]) -> str:
    claim_id = args.get("claim_id", "")
    if not claim_id:
        return _missing("claim_id")
    return _request_json(provider, f"/claims/{claim_id}", None, method="GET", timeout=10.0)


def handle_guards_list(provider: ToolProvider, args: dict[str, Any]) -> str:
    try:
        return _json_result(provider._eb_request(f"/guards/active/{provider._session_id}", None, method="GET", timeout=10.0))
    except OSError as exc:
        if _is_http_status(exc, 404):
            try:
                provider._eb_request(f"/guards/refresh/{provider._session_id}", {"session_key": provider._session_key}, method="POST", timeout=10.0)
                return _json_result(provider._eb_request(f"/guards/active/{provider._session_id}", None, method="GET", timeout=10.0))
            except OSError:
                return _optional_unavailable("guards", "guards_unavailable", "Guard rules are not loaded for this session. Try guards refresh after session bootstrap and verify EB_TIER is full or context_only; this does not affect ordinary memory search/store.")
        if _is_http_status(exc, 503):
            return _optional_unavailable("guards", "guards_unavailable", "Guard rules are not enabled for this deployment. Verify EB_TIER is full or context_only; this does not affect ordinary memory search/store.")
        return _json_result({"error": f"Guards list failed: {exc}"})


def handle_guard_status(provider: ToolProvider, args: dict[str, Any]) -> str:
    guard_event_id = args.get("guard_event_id", "")
    if not guard_event_id:
        return _missing("guard_event_id")
    path = _query(f"/guards/events/detail/{guard_event_id}", {"session_id": provider._session_id})
    return _request_json(provider, path, None, method="GET", timeout=10.0)
