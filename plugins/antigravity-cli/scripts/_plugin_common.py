"""Shared helpers across ElephantBroker plugin hook scripts.

Kept deliberately lean: session key management, logging, EB adapter
delegation. Hook scripts shouldn't grow heavy because they run on
every user prompt / tool call.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_PLUGIN_DIR = Path.home() / ".elephantbroker"
_LEGACY_PLUGIN_DIR = Path.home() / ".cognee-plugin"
_HOOK_LOG = _PLUGIN_DIR / "hook.log"
_COUNTER_FILE = _PLUGIN_DIR / "counter.json"
_ACTIVITY_FILE = _PLUGIN_DIR / "activity.ts"
_ACTIVITY_LOG = _PLUGIN_DIR / "activity.log"
_SAVE_COUNTER = _PLUGIN_DIR / "save_counter.json"
_SYNC_LOCK = _PLUGIN_DIR / "sync.lock"
_HTTP_BRIDGE_CACHE = _PLUGIN_DIR / "http_bridge_cache.json"
_HTTP_BRIDGE_STATE = _PLUGIN_DIR / "http_bridge_state.json"
_PENDING_PROMPTS = _PLUGIN_DIR / "pending_prompts.json"
_SUBPROCESS_LOG = _PLUGIN_DIR / "subprocess.log"

# Save-kinds tracked per turn.
SAVE_KINDS = ("prompt", "trace", "answer")

# Cap the per-line log size.
_LOG_LINE_CAP = 600

SYNC_LOCK_STALE_SECONDS = 15 * 60

# Lazy-imported EB adapter module.
_EB_MODULE = None


def _ensure_plugin_dir() -> None:
    """Ensure the plugin state directory exists, migrate legacy if needed."""
    _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    # Migrate legacy state files from ~/.cognee-plugin/ if they exist
    if _LEGACY_PLUGIN_DIR.exists() and not (_PLUGIN_DIR / "_migrated").exists():
        for name in ("counter.json", "save_counter.json", "pending_prompts.json",
                     "http_bridge_cache.json", "http_bridge_state.json", "activity.ts"):
            src = _LEGACY_PLUGIN_DIR / name
            dst = _PLUGIN_DIR / name
            if src.exists() and not dst.exists():
                try:
                    import shutil
                    shutil.copy2(src, dst)
                except Exception:
                    pass
        try:
            (_PLUGIN_DIR / "_migrated").write_text("1", encoding="utf-8")
        except Exception:
            pass


def _sanitize_session_key(value: str) -> str:
    safe = []
    for ch in str(value or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe).strip("._")[:120]


def get_session_key() -> str:
    candidates = [
        os.environ.get("EB_SESSION_KEY"),
        os.environ.get("COGNEE_SESSION_KEY"),
    ]
    for value in candidates:
        text = _sanitize_session_key(str(value or "").strip())
        if text:
            return text
    return ""


def set_session_key(session_key: str) -> str:
    normalized = _sanitize_session_key(session_key)
    if normalized:
        os.environ["EB_SESSION_KEY"] = normalized
        os.environ["COGNEE_SESSION_KEY"] = normalized  # backward compat
    return normalized


def resolve_session_key_from_payload(payload: dict) -> tuple[str, str]:
    """Resolve session key from a hook payload using known Antigravity variants."""
    if not isinstance(payload, dict):
        return "", "missing_payload"

    def _read_path(obj: dict, path: list[str]) -> str:
        cur = obj
        for key in path[:-1]:
            nxt = cur.get(key)
            if not isinstance(nxt, dict):
                return ""
            cur = nxt
        value = cur.get(path[-1])
        return str(value or "").strip() if value is not None else ""

    candidates: list[tuple[str, list[str]]] = [
        ("payload.conversationId", ["conversationId"]),
        ("payload.conversation_id", ["conversation_id"]),
        ("payload.session_id", ["session_id"]),
        ("payload.sessionId", ["sessionId"]),
        ("payload.session.id", ["session", "id"]),
        ("payload.conversation.id", ["conversation", "id"]),
        ("payload.chat_id", ["chat_id"]),
        ("payload.chatId", ["chatId"]),
        ("payload.thread_id", ["thread_id"]),
        ("payload.threadId", ["threadId"]),
    ]
    for source, path in candidates:
        value = _read_path(payload, path)
        if value:
            return value, source
    return "", "not_found"


def _resolve_agent_name() -> str:
    def _normalize(name: str) -> str:
        raw = str(name or "").strip()
        suffix = "_agy"
        if raw.endswith(suffix):
            return raw
        return f"{raw}{suffix}"

    env_name = str(os.environ.get("EB_AGENT_NAME") or os.environ.get("COGNEE_AGENT_NAME") or "").strip()
    if env_name:
        return _normalize(env_name)
    try:
        from config import load_config
        configured = str(load_config().get("agent_name") or "").strip()
        if configured:
            normalized = _normalize(configured)
            os.environ["EB_AGENT_NAME"] = normalized
            return normalized
    except Exception:
        pass
    return _normalize("antigravity-agent")


def load_resolved(session_key: str = "") -> dict:
    """Load runtime state for the active session (EB mode)."""
    resolved: dict = {}

    active_session_key = _sanitize_session_key(session_key) or get_session_key()
    if active_session_key:
        resolved["session_key"] = active_session_key

    service_url = _service_url_for_eb().strip()
    if service_url:
        resolved["service_url"] = service_url

    dataset = str(os.environ.get("EB_DATASET") or os.environ.get("COGNEE_PLUGIN_DATASET") or "").strip()
    if not dataset:
        try:
            from config import get_dataset, load_config
            dataset = str(get_dataset(load_config()) or "").strip()
        except Exception:
            dataset = ""
    if dataset:
        resolved["dataset"] = dataset
    if active_session_key:
        resolved["session_id"] = active_session_key
        resolved["agent_session_name"] = active_session_key
        resolved["registered"] = True

    return resolved


def _load_json_file(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            hook_log("json_load_failed", {"path": str(path), "error": str(exc)[:200]})
    return {}


def _write_json_file(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        hook_log("json_write_failed", {"path": str(path), "error": str(exc)[:200]})


def _bridge_cache_key(dataset: str, session_id: str) -> str:
    return f"eb:{dataset}:{session_id}"


def append_http_bridge_entry(
    dataset: str,
    session_id: str,
    *,
    question: str = "",
    answer: str = "",
    trace: str = "",
) -> None:
    """Keep a local shadow of session text for graph bridging."""
    if not dataset or not session_id:
        return
    if not (question or answer or trace):
        return

    cache = _load_json_file(_HTTP_BRIDGE_CACHE)
    key = _bridge_cache_key(dataset, session_id)
    session_cache = cache.setdefault(key, {"qa": [], "trace": []})
    if question or answer:
        session_cache.setdefault("qa", []).append({"question": question, "answer": answer})
    if trace:
        session_cache.setdefault("trace", []).append(trace)
    _write_json_file(_HTTP_BRIDGE_CACHE, cache)


def hook_log(event: str, detail: Optional[dict] = None) -> None:
    """Append one structured line to ~/.elephantbroker/hook.log."""
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": event,
        }
        if detail:
            line["detail"] = detail
        serialized = json.dumps(line, default=str)
        if len(serialized) > _LOG_LINE_CAP:
            serialized = serialized[: _LOG_LINE_CAP - 3] + "..."
        with _HOOK_LOG.open("a", encoding="utf-8") as fh:
            fh.write(serialized + "\n")
    except Exception:
        pass


def _verbose_enabled() -> bool:
    return any(
        os.environ.get(k, "").lower() in ("1", "true", "yes")
        for k in ("EB_VERBOSE", "COGNEE_PLUGIN_VERBOSE")
    )


def notify(msg: str) -> None:
    """Print a status line to stderr."""
    line = f"elephantbroker: {msg}"
    print(line, file=sys.stderr)
    if _verbose_enabled():
        try:
            _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with _ACTIVITY_LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts} {line}\n")
        except Exception as exc:
            hook_log("activity_log_write_failed", {"error": str(exc)[:200]})


@contextmanager
def quiet_hook_output(label: str):
    """Redirect stdout/stderr to a plugin log while a hook does EB work."""
    _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    saved_stdout_fd = os.dup(1)
    saved_stderr_fd = os.dup(2)
    log_fd = os.open(_SUBPROCESS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        marker = (
            f"\n--- {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"{label} pid={os.getpid()} ---\n"
        )
        os.write(log_fd, marker.encode("utf-8"))
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        yield
    finally:
        os.dup2(saved_stdout_fd, 1)
        os.dup2(saved_stderr_fd, 2)
        os.close(saved_stdout_fd)
        os.close(saved_stderr_fd)
        os.close(log_fd)


def bump_save_counter(session_id: str, kind: str) -> None:
    if not session_id or kind not in SAVE_KINDS:
        return
    try:
        data = json.loads(_SAVE_COUNTER.read_text(encoding="utf-8")) if _SAVE_COUNTER.exists() else {}
    except Exception:
        data = {}
    sess = data.get(session_id) or {k: 0 for k in SAVE_KINDS}
    sess[kind] = int(sess.get(kind, 0)) + 1
    data[session_id] = sess
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        _SAVE_COUNTER.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        hook_log("save_counter_write_failed", {"error": str(exc)[:200]})


def read_and_reset_save_counter(session_id: str) -> dict:
    zero = {k: 0 for k in SAVE_KINDS}
    if not session_id:
        return zero
    try:
        data = json.loads(_SAVE_COUNTER.read_text(encoding="utf-8")) if _SAVE_COUNTER.exists() else {}
    except Exception:
        return zero
    sess = data.get(session_id) or zero
    data[session_id] = dict(zero)
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        _SAVE_COUNTER.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return {k: int(sess.get(k, 0)) for k in SAVE_KINDS}


def _pending_keys(session_id: str, turn_id: str = "") -> tuple[str, str]:
    session_key = f"{session_id}:"
    turn_key = f"{session_id}:{turn_id}" if turn_id else session_key
    return turn_key, session_key


def remember_pending_prompt(
    session_id: str, prompt: str, *, turn_id: str = "", context: str = ""
) -> None:
    if not session_id or not prompt.strip():
        return
    data = _load_json_file(_PENDING_PROMPTS)
    turn_key, session_key = _pending_keys(session_id, turn_id)
    entry = {
        "prompt": prompt[:8000],
        "context": context[:2000],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data[turn_key] = entry
    data[session_key] = entry
    _write_json_file(_PENDING_PROMPTS, data)


def pop_pending_prompt(session_id: str, *, turn_id: str = "") -> dict:
    if not session_id:
        return {"prompt": "", "context": ""}
    data = _load_json_file(_PENDING_PROMPTS)
    turn_key, session_key = _pending_keys(session_id, turn_id)
    entry = data.pop(turn_key, None) or data.get(session_key) or {}
    data.pop(session_key, None)
    _write_json_file(_PENDING_PROMPTS, data)
    if not isinstance(entry, dict):
        return {"prompt": "", "context": ""}
    return {
        "prompt": str(entry.get("prompt") or ""),
        "context": str(entry.get("context") or ""),
    }


def bump_turn_counter(session_id: str) -> tuple[int, bool]:
    if not session_id:
        return 0, False
    threshold = int(os.environ.get("EB_AUTO_IMPROVE_EVERY", "30") or "30")
    data: dict = {}
    if _COUNTER_FILE.exists():
        try:
            data = json.loads(_COUNTER_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    count = int(data.get(session_id, 0)) + 1
    data[session_id] = count
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        _COUNTER_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    should_improve = threshold > 0 and count % threshold == 0
    return count, should_improve


def touch_activity() -> None:
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        _ACTIVITY_FILE.write_text(str(datetime.now(timezone.utc).timestamp()), encoding="utf-8")
    except Exception:
        pass


@contextmanager
def sync_lock(owner: str):
    acquired = False
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).timestamp()
        if _SYNC_LOCK.exists():
            try:
                current = json.loads(_SYNC_LOCK.read_text(encoding="utf-8"))
                created_at = float(current.get("created_at", 0))
                pid = int(current.get("pid", 0))
            except Exception:
                created_at = 0
                pid = 0
            pid_alive = False
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    pid_alive = True
                except PermissionError:
                    pid_alive = True
                except OSError:
                    pid_alive = False
            if not pid_alive or now - created_at > SYNC_LOCK_STALE_SECONDS:
                try:
                    _SYNC_LOCK.unlink()
                except Exception:
                    pass
        try:
            fd = os.open(str(_SYNC_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"owner": owner, "pid": os.getpid(), "created_at": now}, fh)
            acquired = True
            yield True
        except FileExistsError:
            hook_log("sync_lock_busy", {"owner": owner})
            yield False
    finally:
        if acquired:
            try:
                _SYNC_LOCK.unlink()
            except Exception:
                pass


# ── EB adapter delegation ────────────────────────────────────────────

def _service_url_for_eb() -> str:
    return (os.environ.get("EB_SERVICE_URL") or os.environ.get("COGNEE_SERVICE_URL") or "http://localhost:8420").strip().rstrip("/")


def _eb_module():
    global _EB_MODULE
    if _EB_MODULE is None:
        import importlib
        _EB_MODULE = importlib.import_module("_plugin_eb")
    return _EB_MODULE


def resolved_http_endpoint_auth() -> tuple[str, str]:
    service_url = _service_url_for_eb()
    return service_url, "eb_mode"


def http_api_ready() -> bool:
    return bool(_service_url_for_eb())


def resolve_runtime_mode() -> dict:
    service_url = _service_url_for_eb()
    return {
        "mode": "eb",
        "service_url": service_url,
        "api_key_present": True,
        "url_source": "eb_service_url",
        "key_source": "eb_mode",
    }


def register_agent_via_http(
    *,
    agent_session_name: str,
    session_id: str = "",
    dataset_names: list[str] | None = None,
    timeout: float = 15.0,
) -> tuple[bool, dict]:
    eb = _eb_module()
    result = eb.eb_session_start(agent_session_name, session_id=session_id)
    if result and result.get("status") == "ok":
        return True, result
    hook_log("eb_agent_register_failed", {"agent_session_name": agent_session_name})
    return False, {}


def unregister_agent_via_http(
    *, agent_session_name: str, timeout: float = 15.0
) -> tuple[bool, int]:
    eb = _eb_module()
    result = eb.eb_session_end(
        agent_session_name,
        session_id=agent_session_name,
    )
    if isinstance(result, dict) and result.get("session_key"):
        return True, 0
    return False, 0


def recall_via_http(
    query: str,
    *,
    session_id: str,
    top_k: int,
    scope: list[str],
    only_context: bool = True,
    search_type: str | None = None,
    timeout: float = 60.0,
) -> list:
    eb = _eb_module()
    scope_set = {str(s).lower() for s in (scope or [])}
    if "global" in scope_set and "session" not in scope_set:
        result = eb.eb_search_global(query, max_results=top_k)
    else:
        result = eb.eb_search(query, max_results=top_k, session_key=session_id)
    return result if isinstance(result, list) else []


def _entry_to_text(entry: dict) -> str:
    q = str(entry.get("question") or "").strip()
    a = str(entry.get("answer") or "").strip()
    if q or a:
        parts = [f"Q: {q}", f"A: {a}"] if q and a else [q or a]
        return "\n".join(parts)
    origin = str(entry.get("origin_function") or "?").strip()
    status = str(entry.get("status") or "").strip()
    fb = str(entry.get("session_feedback") or "").strip()
    mrv = str(entry.get("method_return_value") or "").strip()
    parts = [f"[{origin}] {status}".strip()]
    if fb:
        parts.append(f"feedback: {fb}")
    if mrv:
        parts.append(f"output: {mrv[:300]}")
    return "\n".join(parts)


def remember_entry_via_http(
    dataset: str,
    session_id: str,
    entry: dict,
    *,
    timeout: float = 30.0,
) -> dict | None:
    """Stage an entry for final sync via EB ingest-turn."""
    if not dataset or not session_id:
        return None
    text = _entry_to_text(entry)
    entry_type = str(entry.get("type") or "entry")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    hook_log(
        "eb_entry_staged_for_final_sync",
        {
            "dataset": dataset,
            "session": session_id,
            "entry_type": entry_type,
            "chars": len(text),
            "entry_id": digest,
        },
    )
    return {"mode": "staged_for_final_sync", "entry_id": digest}


def persist_session_cache_to_graph_via_http(
    dataset: str,
    session_id: str,
    timeout: float = 600.0,
) -> bool:
    eb = _eb_module()
    return eb.eb_persist_session(dataset, session_id, timeout=timeout)


def _format_cached_bridge_document(dataset: str, session_id: str) -> tuple[str, str]:
    cache = _load_json_file(_HTTP_BRIDGE_CACHE)
    key = _bridge_cache_key(dataset, session_id)
    session_cache = cache.get(key, {})
    qa_lines: list[str] = []
    for entry in session_cache.get("qa", []) or []:
        question = str(entry.get("question") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        if question:
            qa_lines.append(f"Question: {question}")
        if answer:
            qa_lines.append(f"Answer: {answer}")
        if question or answer:
            qa_lines.append("")
    trace_lines = [str(value).strip() for value in session_cache.get("trace", []) or []]
    trace_lines = [value for value in trace_lines if value]
    qa_doc = "\n".join(qa_lines).strip()
    trace_doc = "\n\n".join(trace_lines).strip()
    if qa_doc:
        qa_doc = f"Session ID: {session_id}\n\n{qa_doc}"
    if trace_doc:
        trace_doc = f"Session ID: {session_id}\n\n{trace_doc}"
    return qa_doc, trace_doc
