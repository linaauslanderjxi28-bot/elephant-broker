#!/usr/bin/env python3
"""Close the ElephantBroker session at Antigravity CLI stop.

Conversation facts are persisted only by the PostInvocation explicit-memory gate.
This stop hook deliberately does not replay cached tool outputs or whole sessions.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    get_session_key,
    hook_log,
    resolve_session_key_from_payload,
    set_session_key,
    unregister_agent_via_http,
)
from _plugin_eb import _stable_uuid, eb_session_end
from config import get_session_id, load_config


def _session_key() -> str:
    current = set_session_key(get_session_key())
    if current:
        return current
    return set_session_key(get_session_id(load_config()))


def _close() -> bool:
    session_key = _session_key()
    if not session_key:
        hook_log("session_end_skipped_no_session_key")
        return False
    try:
        result = eb_session_end(session_key, session_id=_stable_uuid(session_key), reason="session_end")
        accepted = isinstance(result, dict) and bool(result.get("session_key"))
        hook_log("session_end_result", {"session_key": session_key, "accepted": accepted})
        return accepted
    except Exception as exc:
        hook_log("session_end_error", {"error": str(exc)[:300]})
        return False


def main() -> None:
    raw = sys.stdin.read()
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        session_key, source = resolve_session_key_from_payload(payload)
        if session_key:
            set_session_key(session_key)
        hook_log("sync_session_key", {"source": source})
    _close()


if __name__ == "__main__":
    main()
