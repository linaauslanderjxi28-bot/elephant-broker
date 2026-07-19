#!/usr/bin/env python3
"""Antigravity CLI PostInvocation adapter with explicit-memory-only writes."""
from __future__ import annotations

import asyncio
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    bump_save_counter,
    get_session_key,
    hook_log,
    load_resolved,
    quiet_hook_output,
    resolve_session_key_from_payload,
    set_session_key,
    touch_activity,
)
from config import get_dataset, get_session_id, load_config
from memory_governance import safe_memory_messages

MAX_TEXT = 4000
_TAIL_LINES = 200
app_data = os.environ.get("ANTIGRAVITY_APP_DATA")
_TRANSCRIPT_BASE = Path(app_data) / "brain" if app_data else Path.home() / ".gemini" / "antigravity-cli" / "brain"


def _extract_answer_from_transcript(conversation_id: str) -> str:
    transcript = _TRANSCRIPT_BASE / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript.exists():
        hook_log("agy_transcript_not_found_post", {"path": str(transcript)})
        return ""
    try:
        tail: collections.deque[str] = collections.deque(maxlen=_TAIL_LINES)
        with transcript.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if raw.strip():
                    tail.append(raw.strip())
        for raw in reversed(tail):
            try:
                step = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if step.get("type") not in {"PLANNER_RESPONSE", "MODEL_RESPONSE", "ASSISTANT"}:
                continue
            content = step.get("content", "")
            if isinstance(content, str):
                return content[:MAX_TEXT].strip()
            if isinstance(content, list):
                return " ".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()[:MAX_TEXT]
    except OSError as exc:
        hook_log("agy_transcript_read_error_post", {"error": str(exc)[:200]})
    return ""


async def _store_qa(payload: dict) -> None:
    conversation_id = str(payload.get("conversationId") or "")
    invocation_num = payload.get("invocationNum", 0)
    config = load_config()
    resolved = load_resolved()
    session_id = str(resolved.get("session_id") or get_session_id(config))
    dataset = str(resolved.get("dataset") or get_dataset(config))
    if not session_id:
        hook_log("agy_post_invocation_no_session_id")
        return

    from _plugin_common import pop_pending_prompt
    pending = pop_pending_prompt(session_id, turn_id=str(invocation_num))
    prompt = pending.get("prompt", "")
    if not prompt:
        hook_log("agy_post_invocation_no_pending_prompt", {"session": session_id})
        return
    answer = _extract_answer_from_transcript(conversation_id)
    messages = safe_memory_messages(prompt, answer)
    if not messages:
        hook_log("agy_post_invocation_skipped_not_explicit_memory_request", {"session": session_id})
        return

    try:
        from _plugin_eb import eb_ingest_turn
        session_key = get_session_key() or session_id
        result = eb_ingest_turn(session_key, messages, session_id=session_id, timeout=45.0)
        accepted = isinstance(result, dict) and result.get("facts_extracted") is not None
        hook_log("agy_post_invocation_explicit_memory_result", {"session": session_id, "dataset": dataset, "accepted": accepted})
        if accepted:
            bump_save_counter(session_id, "answer")
            touch_activity()
    except Exception as exc:
        hook_log("agy_post_invocation_eb_error", {"error": str(exc)[:300]})


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        hook_log("agy_post_invocation_bad_json")
        return
    session_key, source = resolve_session_key_from_payload(payload)
    if session_key:
        set_session_key(session_key)
    hook_log("agy_post_invocation_session_key", {"source": source})
    try:
        with quiet_hook_output("agy-post-invocation"):
            asyncio.run(_store_qa(payload))
    except Exception as exc:
        hook_log("agy_post_invocation_exception", {"error": str(exc)[:300]})


if __name__ == "__main__":
    main()
