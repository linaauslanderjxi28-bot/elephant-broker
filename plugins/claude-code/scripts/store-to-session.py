#!/usr/bin/env python3
"""Store tool calls and assistant responses into session memory.

Routes tool calls into structured trace entries and stores the final assistant
message from the Claude Code `Stop` hook as a remembered QA-style turn.

All operations go through the ElephantBroker backend. Interactive hooks stage
data locally and flush via session-end sync.
"""

import asyncio
import json
import os
import sys

# Add scripts dir to path for helper imports
sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    bump_save_counter,
    bump_turn_counter,
    get_session_key,
    hook_log,
    load_resolved,
    notify,
    pop_pending_prompt,
    quiet_hook_output,
    recall_via_http,
    remember_entry_via_http,
    resolve_runtime_mode,
    resolve_session_key_from_payload,
    set_session_key,
    touch_activity,
)
from config import get_dataset, get_session_id, load_config

# Hard cap per field to avoid ballooning the cache with massive tool outputs.
_MAX_PARAMS_BYTES = 4000
_MAX_RETURN_BYTES = 8000
_MAX_ASSISTANT_BYTES = 8000

# After this many tool calls, inject memory context into the tool output.
_MEMORY_CONTEXT_INTERVAL = 5


def _truncate_str(value, cap: int) -> str:
    """Coerce to string and cap at ``cap`` bytes (utf-8), appending ``...`` if truncated."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return text
    return encoded[: cap - 3].decode("utf-8", errors="ignore") + "..."


def _infer_status(payload: dict) -> tuple[str, str]:
    """Return (status, error_message) from a PostToolUse payload."""
    response = payload.get("tool_response") or payload.get("tool_output") or ""
    if isinstance(response, dict):
        if response.get("is_error") or response.get("error"):
            err = response.get("error") or response.get("message") or "Tool reported an error."
            return "error", _truncate_str(err, 500)
    if isinstance(payload.get("error"), str) and payload["error"]:
        return "error", _truncate_str(payload["error"], 500)
    return "success", ""


def _load_session() -> tuple[str, str]:
    """Load session_id, dataset from resolved cache with fallbacks."""
    resolved = load_resolved()
    session_id = resolved.get("session_id", "")
    dataset = resolved.get("dataset", "")
    if not session_id or not dataset:
        config = load_config()
        session_id = session_id or get_session_id(config)
        dataset = dataset or get_dataset(config)
    return session_id, dataset


async def _store_tool_call(payload: dict) -> str | None:
    """Write a PostToolUse event as a trace entry. Returns memory context if available."""
    tool_name = payload.get("tool_name", "unknown")
    tool_input = payload.get("tool_input") or {}
    tool_output = payload.get("tool_output") or payload.get("tool_response") or ""

    # Suppress self-reference: any Bash call that mentions 'cognee' or 'elephantbroker'
    # is likely the plugin/CLI talking to itself and would recurse.
    if tool_name == "Bash":
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command", ""))
        if "cognee" in cmd or "elephantbroker" in cmd:
            hook_log("skip_self_reference_bash", {"cmd_prefix": cmd[:80]})
            return None

    status, error_message = _infer_status(payload)

    # Normalize method_params
    if isinstance(tool_input, dict):
        params = {}
        for k, v in tool_input.items():
            params[k] = _truncate_str(v, _MAX_PARAMS_BYTES)
    else:
        params = {"value": _truncate_str(tool_input, _MAX_PARAMS_BYTES)}

    return_value = _truncate_str(tool_output, _MAX_RETURN_BYTES)

    session_id, dataset = _load_session()
    if not session_id:
        hook_log("no_session_id", {"tool": tool_name})
        return None

    entry = {
        "type": "trace",
        "origin_function": tool_name,
        "status": status,
        "method_params": params,
        "method_return_value": return_value,
        "error_message": error_message,
    }

    try:
        result = remember_entry_via_http(dataset, session_id, entry)
    except Exception as exc:
        hook_log("trace_store_error", {"tool": tool_name, "error": str(exc)[:200]})
        notify(f"trace store failed ({exc})")
        return None

    if result:
        trace_id = result.get("entry_id") if isinstance(result, dict) else None
        hook_log("trace_stored", {"tool": tool_name, "status": status, "trace_id": trace_id})
        notify(f"trace stored ({tool_name}, {status})")
        bump_save_counter(session_id, "trace")

        touch_activity()
        count, should_improve = bump_turn_counter(session_id)

        # Periodically inject memory context alongside tool results
        if count > 0 and count % _MEMORY_CONTEXT_INTERVAL == 0:
            try:
                ctx = _fetch_memory_context(session_id, tool_name, tool_input)
                if ctx:
                    return ctx
            except Exception as exc:
                hook_log("memory_context_fetch_error", {"error": str(exc)[:200]})
    else:
        hook_log("trace_store_noresult", {"tool": tool_name})

    return None


def _fetch_memory_context(session_id: str, tool_name: str, tool_input: dict) -> str | None:
    """Fetch relevant memory context to inject alongside tool results."""
    # Build a query from the tool input
    query_parts = []
    if isinstance(tool_input, dict):
        for val in tool_input.values():
            if isinstance(val, str) and len(val) > 10:
                query_parts.append(val[:200])
    query = " ".join(query_parts) if query_parts else tool_name

    results = recall_via_http(
        query, session_id=session_id, top_k=3, scope=["session"], timeout=8.0
    )
    try:
        global_results = recall_via_http(
            query, session_id=session_id, top_k=3, scope=["global"], timeout=8.0
        )
    except Exception:
        global_results = None

    # Merge session + global results (session first, then global)
    all_results = list(results or [])
    if global_results:
        all_results.extend(global_results)

    if all_results:
        texts = []
        for r in all_results[:3]:
            if isinstance(r, dict):
                text = str(r.get("text") or r.get("answer") or "")
                if text:
                    texts.append(text[:200])
        if texts:
            return "\n".join(texts)
    return None


async def _store_assistant_stop(payload: dict) -> None:
    """Write a Stop-hook payload (final assistant message) as a QAEntry."""
    msg = str(payload.get("assistant_message") or payload.get("last_assistant_message") or "")
    if not msg or msg == "null":
        return

    msg = _truncate_str(msg, _MAX_ASSISTANT_BYTES)

    session_id, dataset = _load_session()
    if not session_id:
        hook_log("no_session_id", {"event": "stop"})
        return

    pending = pop_pending_prompt(session_id, turn_id=str(payload.get("turn_id") or ""))

    entry = {
        "type": "qa",
        "question": pending.get("prompt", ""),
        "answer": msg,
        "context": pending.get("context", ""),
    }

    try:
        result = remember_entry_via_http(dataset, session_id, entry)
    except Exception as exc:
        hook_log("stop_store_error", {"error": str(exc)[:200]})
        notify(f"stop store failed ({exc})")
        return

    if result:
        qa_id = result.get("entry_id") if isinstance(result, dict) else None
        hook_log("stop_stored", {"chars": len(msg), "qa_id": qa_id})
        notify(f"assistant message stored ({len(msg)} chars)")
        bump_save_counter(session_id, "answer")

        touch_activity()
        count, should_improve = bump_turn_counter(session_id)
        if should_improve:
            hook_log("auto_bridge_triggered", {"reason": f"turn_{count}"})


def main():
    payload_raw = sys.stdin.read()
    if not payload_raw.strip():
        return

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        hook_log("invalid_payload_json")
        return

    session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
    if session_key_candidate:
        set_session_key(session_key_candidate)
    hook_log("store_session_key", {"source": session_key_source, "value": session_key_candidate})
    if not get_session_key():
        hook_log("store_missing_session_key")
        return

    is_stop = "--stop" in sys.argv
    try:
        with quiet_hook_output("store-to-session"):
            if is_stop:
                asyncio.run(_store_assistant_stop(payload))
            else:
                memory_ctx = asyncio.run(_store_tool_call(payload))

                # If we have memory context, inject it as additionalContext
                # via the hook output protocol so Claude sees it alongside
                # the tool result.
                if memory_ctx:
                    output = {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": (
                                "Relevant memory context for this operation:\n"
                                f"{memory_ctx}"
                            ),
                        }
                    }
                    print(json.dumps(output))
    except Exception as exc:
        hook_log("run_exception", {"stop": is_stop, "error": str(exc)[:200]})


if __name__ == "__main__":
    main()
