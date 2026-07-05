#!/usr/bin/env python3
"""Initialize ElephantBroker memory at session start.

Runs on the Antigravity CLI SessionStart hook. Responsibilities:
  1. Load config
  2. Compute session key
  3. Register session with ElephantBroker
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    hook_log,
    notify,
    quiet_hook_output,
    register_agent_via_http,
    resolve_session_key_from_payload,
    set_session_key,
    touch_activity,
    _ensure_plugin_dir,
)
from config import get_dataset, get_session_id, load_config, save_config


def main():
    payload_raw = sys.stdin.read()
    payload = {}
    if payload_raw.strip():
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            hook_log("invalid_payload_json", {"event": "session_start"})

    # Ensure plugin state directory
    _ensure_plugin_dir()

    # Load config
    config = load_config()
    save_config(config)

    # Resolve session key from payload or compute from config
    session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
    if not session_key_candidate:
        session_key_candidate = get_session_id(config)
    session_key = set_session_key(session_key_candidate)

    hook_log(
        "session_start",
        {
            "session_key": session_key,
            "session_key_source": session_key_source,
            "dataset": get_dataset(config),
        },
    )

    if not session_key:
        notify("no session key resolved — memory disabled")
        return

    # Register with ElephantBroker
    try:
        registered, result = register_agent_via_http(
            agent_session_name=session_key,
            session_id=session_key,
            dataset_names=[get_dataset(config)],
        )
        if registered:
            notify(f"session '{session_key}' registered with ElephantBroker")
            hook_log("eb_session_registered", {"session_key": session_key, "result": str(result)[:200]})
        else:
            notify(f"EB registration failed for '{session_key}' — memory may be limited")
            hook_log("eb_session_register_failed", {"session_key": session_key})
    except Exception as exc:
        notify(f"EB connection failed — memory disabled ({str(exc)[:100]})")
        hook_log("eb_session_start_error", {"error": str(exc)[:200]})

    touch_activity()

    # Output session info for the agent
    output = {
        "systemMessage": (
            f"ElephantBroker memory active. Session: {session_key}. "
            f"Dataset: {get_dataset(config)}."
        ),
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
