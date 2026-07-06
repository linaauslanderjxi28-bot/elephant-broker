#!/usr/bin/env python3
"""Antigravity CLI PreInvocation adapter.

Maps Antigravity's PreInvocation hook to:
  1. Look up relevant memory and inject context into the model's system prompt.
  2. Stage the current user prompt for later pairing with the answer.

Antigravity sends PreInvocation payload via stdin as JSON. Known fields:
  - conversationId  (str)   – maps to session key
  - invocationNum   (int)   – which invocation number in the session
  - initialNumSteps (int)   – planned steps

Since Antigravity does NOT pass the user's raw prompt text in PreInvocation,
we read the latest transcript turn from the conversation log to extract it.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    bump_save_counter,
    hook_log,
    quiet_hook_output,
    remember_pending_prompt,
    resolve_runtime_mode,
    resolve_session_key_from_payload,
    set_session_key,
    touch_activity,
    load_resolved,
    notify,
    read_and_reset_save_counter,
    recall_via_http,
)
from config import get_dataset, get_session_id, load_config

MAX_TEXT = 4000
TOP_K = 5
TRUNCATE_ANSWER = 500
TRUNCATE_GRAPH_CTX = 1500
_TAIL_LINES = 200  # Only read the last N lines of transcript for performance

app_data = os.environ.get("ANTIGRAVITY_APP_DATA")
if app_data:
    _TRANSCRIPT_BASE = Path(app_data) / "brain"
else:
    _TRANSCRIPT_BASE = Path.home() / ".gemini" / "antigravity-cli" / "brain"


def _extract_prompt_from_transcript(conversation_id: str) -> str:
    """Read the latest user message from the Antigravity transcript."""
    transcript_path = (
        _TRANSCRIPT_BASE / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    if not transcript_path.exists():
        hook_log("agy_transcript_not_found", {"path": str(transcript_path)})
        return ""
    try:
        # Read only the tail of the file for performance (transcripts can be huge)
        import collections
        tail: collections.deque[str] = collections.deque(maxlen=_TAIL_LINES)
        with transcript_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if stripped:
                    tail.append(stripped)
        # Walk backwards to find the latest USER_INPUT step
        for line in reversed(tail):
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            if step.get("type") == "USER_INPUT":
                content = step.get("content", "")
                if isinstance(content, str) and len(content) >= 5:
                    return content[:MAX_TEXT]
                # content may be a list of dicts (multi-part)
                if isinstance(content, list):
                    texts = [
                        p.get("text", "") for p in content if isinstance(p, dict)
                    ]
                    joined = " ".join(t for t in texts if t).strip()
                    if len(joined) >= 5:
                        return joined[:MAX_TEXT]
    except Exception as exc:
        hook_log("agy_transcript_read_error", {"error": str(exc)[:200]})
    return ""


def _format_entry(entry: dict) -> str:
    source = entry.get("source", "")
    if source == "graph_context":
        content = str(entry.get("content", "") or entry.get("text", ""))[:TRUNCATE_GRAPH_CTX]
        return f"[graph-snapshot]\n{content}"
    if source == "trace":
        origin = entry.get("origin_function", "?")
        status = entry.get("status", "")
        mrv = str(entry.get("method_return_value", ""))[:400]
        return f"[trace] {origin} — {status}\n  output: {mrv}" if mrv else f"[trace] {origin} — {status}"
    q = entry.get("question", "")
    a = entry.get("answer", "")
    t = entry.get("time", "")
    lines = []
    if q:
        lines.append(f"[{t}] Q: {q}")
    if a:
        a_short = a[:TRUNCATE_ANSWER] + "..." if len(a) > TRUNCATE_ANSWER else a
        lines.append(f"A: {a_short}")
    return "\n".join(lines)


def _has_entry_content(entry: dict) -> bool:
    source = entry.get("source", "")
    if source == "graph_context":
        return bool(str(entry.get("content", "") or entry.get("text", "")).strip())
    if source == "trace":
        fields = ("origin_function", "status", "session_feedback", "method_return_value")
    else:
        fields = ("question", "answer")
    return any(str(entry.get(f, "") or "").strip() for f in fields)


async def _recall_context(prompt: str, session_id: str) -> str:
    """Search memory and return formatted context string."""
    saves_last_turn = read_and_reset_save_counter(session_id)
    results: list = []
    scope_specs = [
        (["session"], None),
        (["trace"], None),
        (["graph_context"], None),
        (["global"], None),
    ]

    for scope_list, _ in scope_specs:
        try:
            part = recall_via_http(
                prompt,
                session_id=session_id,
                top_k=TOP_K,
                scope=scope_list,
                only_context=True,
            )
            if part:
                results.extend(part)
        except Exception as exc:
            hook_log("recall_error", {"scope": scope_list, "error": str(exc)[:200]})

    by_source: dict[str, list] = {"session": [], "trace": [], "graph_context": [], "global": []}
    for r in results or []:
        if hasattr(r, "model_dump"):
            r = r.model_dump()
        if not isinstance(r, dict):
            continue
        src = r.get("source", "session")
        if src == "graph":
            r["source"] = "graph_context"
            src = "graph_context"
        if not _has_entry_content(r):
            continue
        by_source.setdefault(src, []).append(r)

    counts = {k: len(v) for k, v in by_source.items()}
    total = sum(counts.values())

    header = (
        f"ElephantBroker memory: recall "
        f"{counts['session']} session / {counts['trace']} trace / "
        f"{counts['graph_context']} graph / {counts['global']} global; saved last turn "
        f"{saves_last_turn.get('prompt',0)} prompt / "
        f"{saves_last_turn.get('trace',0)} trace / "
        f"{saves_last_turn.get('answer',0)} answer"
    )

    section_lines = []
    for src_key, label in [
        ("graph_context", "=== Knowledge graph snapshot ==="),
        ("trace", "=== Prior agent trace ==="),
        ("session", "=== Prior session turns ==="),
        ("global", "=== Global (cross-session) knowledge ==="),
    ]:
        if by_source.get(src_key):
            section_lines.append(label)
            for e in by_source[src_key]:
                section_lines.append(_format_entry(e))
                section_lines.append("")

    if total > 0:
        hook_log("agy_context_lookup_hit", {"counts": counts})
        notify(f"[agy] injected context ({counts})")
        return f"{header}\n\nRelevant context from memory:\n\n" + "\n".join(section_lines).strip()
    else:
        hook_log("agy_context_lookup_empty", {"saves_last_turn": saves_last_turn})
        return f"{header}\n\n(no memory matches for this prompt)"


async def _run(payload: dict) -> dict | None:
    conversation_id = payload.get("conversationId", "")
    if not conversation_id:
        hook_log("agy_pre_invocation_no_conversation_id")
        return None

    # Extract the user's prompt from transcript
    prompt = _extract_prompt_from_transcript(conversation_id)
    if not prompt:
        hook_log("agy_pre_invocation_no_prompt", {"conversation_id": conversation_id})
        return None

    hook_log("agy_pre_invocation_prompt", {
        "conversation_id": conversation_id,
        "chars": len(prompt),
        "invocation_num": payload.get("invocationNum", 0),
    })

    config = load_config()
    session_id = load_resolved().get("session_id") or get_session_id(config)
    dataset = load_resolved().get("dataset") or get_dataset(config)

    if not session_id:
        hook_log("agy_pre_invocation_no_session_id")
        return None

    touch_activity()

    # Stage prompt for pairing with the answer (same as UserPromptSubmit did)
    remember_pending_prompt(
        session_id,
        prompt,
        turn_id=str(payload.get("invocationNum", "")),
        context=json.dumps({"conversationId": conversation_id}, default=str),
    )
    bump_save_counter(session_id, "prompt")
    hook_log("agy_prompt_staged", {"session": session_id, "chars": len(prompt)})

    # Recall context and build inject steps
    context_str = await _recall_context(prompt, session_id)

    # Antigravity PreInvocation can return `injectSteps` to prepend content
    # into the model's context before it reasons.
    inject_steps = [
        {
            "type": "text",
            "text": context_str,
        }
    ]

    return {"injectSteps": inject_steps}


def main():
    payload_raw = sys.stdin.read()
    if not payload_raw.strip():
        hook_log("agy_pre_invocation_empty_stdin")
        return

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        hook_log("agy_pre_invocation_bad_json")
        return

    # Set session key from conversationId (Antigravity's identifier)
    session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
    if session_key_candidate:
        set_session_key(session_key_candidate)
    hook_log("agy_pre_invocation_session_key", {
        "source": session_key_source,
        "value": session_key_candidate,
    })

    try:
        with quiet_hook_output("agy-pre-invocation"):
            result = asyncio.run(_run(payload))
    except Exception as exc:
        hook_log("agy_pre_invocation_exception", {"error": str(exc)[:300]})
        return

    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
