#!/usr/bin/env python3
"""Bridge session cache entries into the permanent knowledge graph on session end.

Runs the integration's explicit session bridge:
  1. Persist session Q&A/trace cache into the permanent graph
  2. Sync graph knowledge back into the session cache for recall

Configuration:
    Resolves session identity from ElephantBroker runtime state.
"""

import asyncio
import hashlib
import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Add scripts dir to path for config/_plugin_common imports
sys.path.insert(0, os.path.dirname(__file__))
_plugin_common = importlib.import_module("_plugin_common")
get_session_key = _plugin_common.get_session_key
hook_log = _plugin_common.hook_log
load_resolved = _plugin_common.load_resolved
persist_session_cache_to_graph_via_http = _plugin_common.persist_session_cache_to_graph_via_http
resolve_session_key_from_payload = _plugin_common.resolve_session_key_from_payload
set_session_key = _plugin_common.set_session_key
unregister_agent_via_http = _plugin_common.unregister_agent_via_http

_STATE_DIR = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".elephantbroker")
_WATCHER_PID = _STATE_DIR / "watcher.pid"
_WATCHER_STOP = _STATE_DIR / "watcher.stop"
_DETACHED_ARG = "--detached-final"
_SESSION_END_ARG = "--session-end"
_FINAL_SYNC_ONCE_DIR = _STATE_DIR / "final-sync-once"
_FINAL_SYNC_ONCE_TTL_SECONDS = 3600
_DETACHED_RETRIES_DEFAULT = 3
_DETACHED_RETRY_DELAY_DEFAULT = 10.0
_SESSION_END_START_DELAY_DEFAULT = 2.0


def _stop_idle_watcher() -> None:
    """Signal the idle watcher to exit and drop its pidfile.

    Uses both a sentinel file (safe, polled by the watcher) and a
    SIGTERM (fast). Either path is sufficient; both together handle
    the SIGTERM-blocked-during-improve edge case.
    """
    try:
        _WATCHER_STOP.parent.mkdir(parents=True, exist_ok=True)
        _WATCHER_STOP.write_text("stop", encoding="utf-8")
    except Exception as exc:
        hook_log("watcher_stop_write_failed", {"error": str(exc)[:200]})
    if _WATCHER_PID.exists():
        try:
            pid = int(_WATCHER_PID.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            hook_log("watcher_sigterm_failed", {"error": str(exc)[:200]})


def _spawn_detached_sync() -> bool:
    """Run the expensive sync outside a short hook window."""
    try:
        env = os.environ.copy()
        env.setdefault("COGNEE_SYNC_START_DELAY", str(_SESSION_END_START_DELAY_DEFAULT))
        env["COGNEE_UNREGISTER_ON_FINISH"] = "1"
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), _DETACHED_ARG],
            cwd=os.getcwd(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as exc:
        hook_log("sync_detach_failed", {"error": str(exc)[:300]})
        return False


def _final_sync_identity() -> tuple[str, str]:
    """Return a stable per-session token for detached final sync dedupe."""
    session_key = str(os.environ.get("COGNEE_SESSION_KEY", "") or "").strip()
    if session_key:
        return session_key, "COGNEE_SESSION_KEY"
    session_id = str(os.environ.get("COGNEE_SYNC_SESSION_ID", "") or "").strip()
    if session_id:
        return session_id, "COGNEE_SYNC_SESSION_ID"
    return "", "missing"


def _claim_final_sync_once() -> bool:
    """Allow exactly one detached final sync worker per session."""
    _prune_final_sync_markers()

    token, source = _final_sync_identity()
    if not token:
        # No stable identity available; do not risk skipping final sync.
        hook_log("final_sync_once_no_token", {"source": source})
        return True
    marker = _FINAL_SYNC_ONCE_DIR / f"{hashlib.sha1(token.encode('utf-8')).hexdigest()}.done"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        hook_log("final_sync_once_claimed", {"source": source, "marker": str(marker)})
        return True
    except FileExistsError:
        hook_log("final_sync_once_already_claimed", {"source": source, "marker": str(marker)})
        return False
    except Exception as exc:
        hook_log("final_sync_once_claim_failed", {"source": source, "error": str(exc)[:200]})
        return True


def _release_final_sync_claim() -> None:
    token, source = _final_sync_identity()
    if not token:
        return
    marker = _FINAL_SYNC_ONCE_DIR / f"{hashlib.sha1(token.encode('utf-8')).hexdigest()}.done"
    try:
        marker.unlink(missing_ok=True)
        hook_log("final_sync_once_released", {"source": source, "marker": str(marker)})
    except OSError as exc:
        hook_log("final_sync_once_release_failed", {"source": source, "error": str(exc)[:200]})

    return


def _prune_final_sync_markers() -> None:
    """Delete stale detached-sync dedupe markers older than configured TTL."""
    try:
        if not _FINAL_SYNC_ONCE_DIR.exists():
            return
        now = time.time()
        removed = 0
        for path in _FINAL_SYNC_ONCE_DIR.glob("*.done"):
            try:
                age = now - path.stat().st_mtime
                if age > _FINAL_SYNC_ONCE_TTL_SECONDS:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
            except Exception:
                continue
        if removed:
            hook_log(
                "final_sync_once_pruned",
                {"removed": removed, "ttl_seconds": _FINAL_SYNC_ONCE_TTL_SECONDS},
            )
    except Exception as exc:
        hook_log("final_sync_once_prune_failed", {"error": str(exc)[:200]})


def _is_session_end_payload(payload_raw: str) -> bool:
    """Return True only for an actual SessionEnd hook payload."""
    if not payload_raw.strip():
        return False
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return False

    def _contains_session_end(value) -> bool:
        if isinstance(value, dict):
            return any(_contains_session_end(item) for item in value.values())
        if isinstance(value, list):
            return any(_contains_session_end(item) for item in value)
        if isinstance(value, str):
            return value == "SessionEnd" or value.endswith(".SessionEnd")
        return False

    event = (
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("event")
        or payload.get("hook")
    )
    return event == "SessionEnd" or _contains_session_end(payload)


def _load_resolved() -> tuple[str, str, str, str, bool, bool, str]:
    """
    Load session ID, dataset, user ID,
    agent session name, registration marker, and API key marker.
    """
    session_key = set_session_key(get_session_key())
    env_session_id = str(os.environ.get("COGNEE_SYNC_SESSION_ID", "") or "").strip()
    env_dataset = str(os.environ.get("COGNEE_SYNC_DATASET", "") or "").strip()
    env_agent_session_name = str(os.environ.get("COGNEE_AGENT_SESSION_NAME", "") or "").strip()
    env_service_url = str(os.environ.get("COGNEE_SERVICE_URL", "") or "").strip()
    has_eb_auth = any(
        str(os.environ.get(name, "") or "").strip()
        for name in ("EB_AUTH_TOKEN", "EB_AGENT_KEY", "EB_GATEWAY_ID")
    )

    if not session_key:
        hook_log("sync_missing_session_key")
    data = load_resolved(session_key=session_key)
    if data:
        service_url = env_service_url or str(data.get("service_url", "") or "").strip()
        if service_url:
            os.environ["COGNEE_SERVICE_URL"] = service_url
        if data.get("user_id"):
            os.environ["COGNEE_USER_ID"] = str(data.get("user_id"))
        return (
            env_session_id or data.get("session_id", ""),
            env_dataset or data.get("dataset", ""),
            data.get("user_id", ""),
            env_agent_session_name or data.get("agent_session_name", ""),
            bool(data.get("registered", False)),
            has_eb_auth,
            session_key,
        )

    fallback_agent_session_name = session_key or ""
    if env_service_url:
        os.environ["COGNEE_SERVICE_URL"] = env_service_url
    return (
        env_session_id or session_key or "claude_session",
        env_dataset or "claude_sessions",
        "",
        env_agent_session_name or fallback_agent_session_name,
        False,
        has_eb_auth,
        session_key,
    )


async def _sync(stop_watcher: bool, unregister_on_finish: bool = False):
    session_id, dataset, user_id, agent_session_name, was_registered, has_eb_auth, session_key = (
        _load_resolved()
    )
    hook_log(
        "sync_start",
        {
            "session": session_id,
            "dataset": dataset,
            "user_id": user_id,
            "stop_watcher": stop_watcher,
        },
    )

    if stop_watcher:
        _stop_idle_watcher()
        hook_log("sync_stopped_watcher", {"session": session_id, "dataset": dataset})

    result = persist_session_cache_to_graph_via_http(dataset, session_id)
    hook_log(
        "sync_bridge_done",
        {"session": session_id, "dataset": dataset, "via": "eb_flush", "status": result.status.value},
    )
    print(
        f"cognee-sync: dataset={dataset} session={session_id} via=eb_flush status={result.status.value}",
        file=sys.stderr,
    )
    if unregister_on_finish and result.terminal_success:
            if not (was_registered or has_eb_auth):
                hook_log(
                    "agent_unregister_skipped_no_auth",
                    {"session": session_id, "dataset": dataset},
                )
            else:
                unregister_name = str(agent_session_name or session_key or "").strip()
                if not unregister_name:
                    hook_log(
                        "agent_unregister_skipped_no_session_name",
                        {"session": session_id, "dataset": dataset},
                    )
                    return
                ok, active = unregister_agent_via_http(agent_session_name=unregister_name)
                hook_log(
                    "agent_unregister_result",
                    {
                        "session": session_id,
                        "dataset": dataset,
                        "agent_session_name": unregister_name,
                        "ok": ok,
                        "active_agents": active,
                        "cached_registered": was_registered,
                    },
                )
    return result


def main():
    detached_final = _DETACHED_ARG in sys.argv
    forced_session_end = _SESSION_END_ARG in sys.argv
    payload_raw = "" if detached_final else sys.stdin.read()
    if not detached_final and payload_raw.strip():
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}
        session_key_candidate, session_key_source = resolve_session_key_from_payload(payload)
        if session_key_candidate:
            set_session_key(session_key_candidate)
        hook_log("sync_session_key", {"source": session_key_source, "value": session_key_candidate})
    is_session_end = forced_session_end or _is_session_end_payload(payload_raw)
    hook_log(
        "sync_payload",
        {
            "is_session_end": is_session_end,
            "detached_final": detached_final,
            "forced_session_end": forced_session_end,
            "payload_preview": payload_raw[:200],
        },
    )

    if detached_final:
        delay_raw = os.environ.get("COGNEE_SYNC_START_DELAY", "")
        try:
            delay = float(delay_raw) if delay_raw else 0.0
        except ValueError:
            delay = 0.0
        if delay > 0:
            hook_log("sync_start_delayed", {"seconds": delay})
            time.sleep(delay)
        if not _claim_final_sync_once():
            hook_log("sync_detached_skipped_duplicate")
            return

    unregister_on_finish = detached_final and os.environ.get(
        "COGNEE_UNREGISTER_ON_FINISH", ""
    ).lower() in ("1", "true", "yes")

    # Only a true SessionEnd should stop the watcher. Manual syncs and
    # slash-command invocations happen mid-session, and killing the watcher
    # there prevents later idle persistence.
    if is_session_end:
        _stop_idle_watcher()
        spawned = _spawn_detached_sync()
        hook_log("sync_deferred_to_shutdown_worker", {"spawned": spawned})
        return

    attempts = 1
    retry_delay = 0.0
    if detached_final:
        attempts = int(os.environ.get("COGNEE_SYNC_RETRIES", str(_DETACHED_RETRIES_DEFAULT)))
        retry_delay = float(
            os.environ.get("COGNEE_SYNC_RETRY_DELAY", str(_DETACHED_RETRY_DELAY_DEFAULT))
        )

    completed_successfully = False
    for attempt in range(1, max(1, attempts) + 1):
        try:
            final_result = asyncio.run(
                _sync(stop_watcher=False, unregister_on_finish=unregister_on_finish)
            )
            if final_result.terminal_success:
                completed_successfully = True
                return
            hook_log(
                "sync_failed",
                {"attempt": attempt, "attempts": attempts, "status": final_result.status.value},
            )
        except Exception as exc:
            # Non-fatal: session sync failure should not crash Codex.
            hook_log(
                "sync_failed",
                {"attempt": attempt, "attempts": attempts, "error": str(exc)[:300]},
            )
            print(f"cognee-sync: failed ({exc})", file=sys.stderr)
        if attempt < attempts:
            hook_log("sync_retry_scheduled", {"attempt": attempt + 1, "delay": retry_delay})
            time.sleep(retry_delay)
    if detached_final and not completed_successfully:
        _release_final_sync_claim()


if __name__ == "__main__":
    main()
