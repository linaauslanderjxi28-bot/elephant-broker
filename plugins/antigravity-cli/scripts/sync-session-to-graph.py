#!/usr/bin/env python3
"""Bridge session cache entries into the permanent knowledge graph on session end.

Configuration:
    Resolves session identity and flushes to ElephantBroker.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add scripts dir to path for config/_plugin_common imports
sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    get_session_key,
    hook_log,
    load_resolved,
    persist_session_cache_to_graph_via_http,
    resolve_session_key_from_payload,
    set_session_key,
    unregister_agent_via_http,
    notify,
)
from config import (
    get_dataset,
    get_session_id,
    load_config,
)


def _load_resolved() -> tuple:
    session_key = set_session_key(get_session_key())
    data = load_resolved(session_key=session_key)
    if data:
        return (
            data.get("session_id", ""),
            data.get("dataset", ""),
            session_key,
        )

    config = load_config()
    fallback_session_id = get_session_id(config)
    return (
        fallback_session_id,
        get_dataset(config),
        session_key,
    )


async def _sync(unregister: bool = False):
    session_id, dataset, session_key = _load_resolved()
    hook_log(
        "sync_start",
        {
            "session": session_id,
            "dataset": dataset,
        },
    )

    try:
        # Since it is EB-only, we call the HTTP bridge persistence
        wrote = persist_session_cache_to_graph_via_http(dataset, session_id)
        hook_log(
            "sync_bridge_done",
            {
                "session": session_id,
                "dataset": dataset,
                "via": "eb_flush",
                "wrote": wrote,
            },
        )
        print(
            "elephantbroker-sync: "
            f"dataset={dataset} session={session_id} via=eb_flush wrote={wrote}",
            file=sys.stderr,
        )
    except Exception as exc:
        hook_log("sync_failed", {"error": str(exc)[:300]})
        print(f"elephantbroker-sync: failed ({exc})", file=sys.stderr)
    finally:
        if unregister:
            unregister_name = str(session_key or session_id or "").strip()
            if unregister_name:
                ok, _ = unregister_agent_via_http(agent_session_name=unregister_name)
                hook_log(
                    "agent_unregister_result",
                    {
                        "session": session_id,
                        "dataset": dataset,
                        "agent_session_name": unregister_name,
                        "ok": ok,
                    },
                )


def main():
    forced_session_end = "--session-end" in sys.argv
    payload_raw = sys.stdin.read()
    if payload_raw.strip():
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}
        session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
        if session_key_candidate:
            set_session_key(session_key_candidate)
        hook_log("sync_session_key", {"source": session_key_source, "value": session_key_candidate})

    try:
        # Run sync synchronously
        asyncio.run(_sync(unregister=forced_session_end))
    except Exception as exc:
        hook_log("sync_main_exception", {"error": str(exc)[:300]})


if __name__ == "__main__":
    main()
