#!/usr/bin/env python3
"""Antigravity CLI PostInvocation adapter.

Maps Antigravity's PostInvocation hook to: pair the staged user prompt with
the model's answer and persist the QA turn to the ElephantBroker memory backend.

Antigravity sends PostInvocation payload via stdin as JSON. Known fields:
  - conversationId  (str)  – maps to session key
  - invocationNum   (int)  – which invocation just finished

We read the latest model response from the transcript to get the answer text.
"""

import asyncio
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
    notify,
    pop_pending_prompt,
    quiet_hook_output,
    resolve_runtime_mode,
    resolve_session_key_from_payload,
    set_session_key,
    touch_activity,
)
from config import get_dataset, get_session_id, load_config

MAX_TEXT = 4000
_TAIL_LINES = 200

app_data = os.environ.get("ANTIGRAVITY_APP_DATA")
if app_data:
    _TRANSCRIPT_BASE = Path(app_data) / "brain"
else:
    _TRANSCRIPT_BASE = Path.home() / ".gemini" / "antigravity-cli" / "brain"


def _extract_answer_from_transcript(conversation_id: str) -> str:
    """Read the latest PLANNER_RESPONSE / model text from the Antigravity transcript."""
    transcript_path = (
        _TRANSCRIPT_BASE / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    if not transcript_path.exists():
        hook_log("agy_transcript_not_found_post", {"path": str(transcript_path)})
        return ""
    try:
        import collections
        tail: collections.deque[str] = collections.deque(maxlen=_TAIL_LINES)
        with transcript_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if stripped:
                    tail.append(stripped)
        
        for line in reversed(tail):
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            step_type = step.get("type", "")
            if step_type in ("PLANNER_RESPONSE", "MODEL_RESPONSE", "ASSISTANT"):
                content = step.get("content", "")
                if isinstance(content, str) and len(content) >= 5:
                    return content[:MAX_TEXT]
                if isinstance(content, list):
                    texts = [
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    joined = " ".join(t for t in texts if t).strip()
                    if len(joined) >= 5:
                        return joined[:MAX_TEXT]
    except Exception as exc:
        hook_log("agy_transcript_read_error_post", {"error": str(exc)[:200]})
    return ""


async def _store_qa(payload: dict) -> None:
    """Pair pending prompt with model answer and persist as a QA memory entry."""
    conversation_id = payload.get("conversationId", "")
    invocation_num = payload.get("invocationNum", 0)

    config = load_config()
    resolved = load_resolved()
    session_id = resolved.get("session_id") or get_session_id(config)
    dataset = resolved.get("dataset") or get_dataset(config)

    if not session_id:
        hook_log("agy_post_invocation_no_session_id")
        return

    touch_activity()

    # Retrieve staged prompt (set during PreInvocation)
    pending = pop_pending_prompt(session_id, turn_id=str(invocation_num))
    prompt_text = pending.get("prompt", "")

    if not prompt_text:
        hook_log("agy_post_invocation_no_pending_prompt", {
            "session": session_id,
            "invocation_num": invocation_num,
        })
        return

    # Extract model answer from transcript
    answer_text = _extract_answer_from_transcript(conversation_id)

    if not answer_text:
        hook_log("agy_post_invocation_no_answer", {"conversation_id": conversation_id})
        return

    hook_log("agy_post_invocation_qa_ready", {
        "session": session_id,
        "prompt_chars": len(prompt_text),
        "answer_chars": len(answer_text),
    })

    # Persist QA pair via ElephantBroker
    try:
        session_key = get_session_key() or session_id
        messages = [
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": answer_text},
        ]
        from _plugin_eb import eb_ingest_turn, eb_persist_session
        eb_ingest_turn(session_key, messages, session_id=session_id)
        bump_save_counter(session_id, "answer")
        hook_log("agy_post_invocation_qa_stored", {
            "session": session_id,
            "dataset": dataset,
        })
        notify(f"[agy] QA stored ({len(prompt_text)}q / {len(answer_text)}a chars)")
        
        # Trigger final sync to graph
        eb_persist_session(dataset=dataset, session_id=session_id)
        hook_log("agy_post_invocation_sync_triggered", {"session": session_id})
    except Exception as exc:
        hook_log("agy_post_invocation_eb_error", {"error": str(exc)[:300]})


def main():
    payload_raw = sys.stdin.read()
    if not payload_raw.strip():
        hook_log("agy_post_invocation_empty_stdin")
        return

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        hook_log("agy_post_invocation_bad_json")
        return

    session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
    if session_key_candidate:
        set_session_key(session_key_candidate)
    hook_log("agy_post_invocation_session_key", {
        "source": session_key_source,
        "value": session_key_candidate,
    })

    try:
        with quiet_hook_output("agy-post-invocation"):
            asyncio.run(_store_qa(payload))
    except Exception as exc:
        hook_log("agy_post_invocation_exception", {"error": str(exc)[:300]})


if __name__ == "__main__":
    main()
