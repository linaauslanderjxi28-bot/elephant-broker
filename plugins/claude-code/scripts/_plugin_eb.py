"""ElephantBroker adapter for Claude Code memory plugin.

Replaces the Cognee-specific HTTP API calls in ``_plugin_common.py`` with
ElephantBroker API calls. When configured, all memory operations (store,
recall, session lifecycle) go through EB's endpoints instead of Cognee's
native knowledge-graph API.

Activation
----------
Set ``COGNEE_SERVICE_URL`` to the EB server address (defaults to
``http://localhost:8420``) and ensure ``COGNEE_API_KEY`` is empty/unset so
the plugin picks EB mode. The adapter is imported by ``_plugin_common.py``
when ``EB_GATEWAY_ID`` or EB-style URLs are detected.

API mapping
-----------
Cognee endpoint                → EB endpoint
------------------------------   ---------------------
POST /api/v1/remember/entry     local stage, final /memory/ingest-turn
POST /api/v1/recall             POST /memory/search
POST /api/v1/agents/register    POST /sessions/start
POST /api/v1/agents/unregister  POST /sessions/end
GET  /health                    GET  /health/  (or /memory/status)
POST /api/v1/cognify            POST /memory/ingest-messages
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


def _stable_uuid(text: str) -> str:
    """Derive a deterministic UUID string from arbitrary text.

    ``POST /memory/ingest-turn`` requires ``session_id`` to be a valid
    UUID. This helper produces one deterministically from any string so
    we can always provide a compliant value.
    """
    if not text:
        return str(uuid.UUID(int=0))
    try:
        return str(uuid.UUID(text))
    except (ValueError, TypeError):
        pass
    return str(uuid.UUID(hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]))

_PLUGIN_DIR = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".elephantbroker")


class PersistStatus(str, Enum):
    FLUSHED = "flushed"
    EMPTY = "empty"
    UNCHANGED = "unchanged"
    LOCK_BUSY = "lock_busy"
    HEALTH_FAILED = "health_failed"
    INGEST_FAILED = "ingest_failed"
    BACKEND_REJECTED = "backend_rejected"
    ACK_FAILED = "ack_failed"


@dataclass(frozen=True, slots=True)
class PersistResult:
    status: PersistStatus
    message_count: int = 0

    @property
    def terminal_success(self) -> bool:
        return self.status in {
            PersistStatus.FLUSHED,
            PersistStatus.EMPTY,
            PersistStatus.UNCHANGED,
        }

    @property
    def retryable(self) -> bool:
        return not self.terminal_success

    def __bool__(self) -> bool:
        return self.status is PersistStatus.FLUSHED

# ── helpers ──────────────────────────────────────────────────────────


def _service_url() -> str:
    """Return EB base URL from env or default."""
    return (os.environ.get("EB_SERVICE_URL") or os.environ.get("EB_RUNTIME_URL") or os.environ.get("COGNEE_SERVICE_URL") or "http://localhost:8420").strip().rstrip("/")


def _gateway_id() -> str:
    return os.environ.get("EB_GATEWAY_ID", "").strip()


def _default_headers() -> dict[str, str]:
    """Build HTTP headers for EB requests.

    EB AuthMiddleware is a stub (always passes), but the GatewayIdentityMiddleware
    reads ``X-EB-Gateway-ID`` and ``X-EB-Agent-Key`` to stamp ``request.state``.
    We set them from env so downstream routes (session start/end) receive
    valid identity.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    gw_id = _gateway_id()
    if gw_id:
        headers["X-EB-Gateway-ID"] = gw_id
    agent_key = os.environ.get("EB_AGENT_KEY", "").strip()
    if agent_key:
        headers["X-EB-Agent-Key"] = agent_key
    actor_id = os.environ.get("EB_ACTOR_ID", "").strip()
    if actor_id:
        headers["X-EB-Actor-Id"] = actor_id
    auth_token = os.environ.get("EB_AUTH_TOKEN", "").strip()
    if auth_token:
        headers["X-EB-Auth-Token"] = auth_token
    return headers


def _eb_request(
    path: str,
    payload: dict | None = None,
    *,
    method: str = "POST",
    timeout: float = 30.0,
):
    """Low-level HTTP helper — mirrors ``_json_http_request`` in plugin_common."""
    if not _gateway_id():
        _hook_log("request_skipped_no_gateway_id", {"path": path, "method": method})
        return None
    base = _service_url()
    headers = _default_headers()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        if not body:
            return None
        return json.loads(body)


def _hook_log(event: str, detail: dict | None = None) -> None:
    """Minimal structured log, same format as _plugin_common's hook_log."""
    try:
        _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": f"eb_{event}",
        }
        if detail:
            line["detail"] = detail
        log_path = _PLUGIN_DIR / "hook.log"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


# ── health / connectivity ────────────────────────────────────────────


def eb_health(timeout: float = 5.0) -> bool:
    """Ping EB health endpoint."""
    try:
        result = _eb_request("/health/", method="GET", timeout=timeout)
        return isinstance(result, dict) and result.get("status") in ("ok", "ready")
    except Exception:
        return False


def eb_memory_status(timeout: float = 5.0) -> dict:
    """Return memory subsystem status from EB."""
    try:
        return _eb_request("/memory/status", method="GET", timeout=timeout) or {}
    except Exception:
        return {}


# ── store ────────────────────────────────────────────────────────────


def eb_store_entry(
    text: str,
    *,
    category: str = "general",
    scope: str = "session",
    memory_class: str = "episodic",
    session_key: str | None = None,
    session_id: str | None = None,
    confidence: float = 1.0,
    timeout: float = 10.0,
) -> dict | None:
    """Store a fact via ``POST /memory/store``.

    This replaces Cognee's ``/api/v1/remember/entry``.
    ``text`` is the fact body (e.g. a QA pair or trace summary).
    """
    fact: dict[str, object] = {
        "text": text,
        "category": category,
        "scope": scope,
        "memory_class": memory_class,
        "confidence": confidence,
    }
    payload: dict[str, object] = {"fact": fact}
    if session_key:
        payload["session_key"] = session_key
    if session_id:
        try:
            payload["session_id"] = str(uuid.UUID(session_id))
        except (ValueError, TypeError):
            pass
    return _eb_request("/memory/store", payload, timeout=timeout)


# ── search / recall ──────────────────────────────────────────────────


def eb_search(
    query: str,
    *,
    max_results: int = 20,
    min_score: float = 0.0,
    session_key: str | None = None,
    session_id: str | None = None,
    scope: str | None = None,
    memory_class: str | None = None,
    auto_recall: bool = False,
    include_audit: bool = False,
    timeout: float = 22.0,
) -> list[dict]:
    """Search memory via ``POST /memory/search``.

    Replaces Cognee's ``/api/v1/recall``. Returns a list of result dicts
    with keys ``text``, ``category``, ``scope``, ``memory_class``,
    ``score``, ``source``, and ``session_key``.
    """
    payload: dict[str, object] = {
        "query": query,
        "max_results": max_results,
        "min_score": min_score,
        "auto_recall": auto_recall,
        "include_audit": include_audit,
    }
    if session_key:
        payload["session_key"] = session_key
    if session_id:
        try:
            sid = str(uuid.UUID(session_id))
        except (ValueError, TypeError):
            sid = _stable_uuid(session_id)
        payload["session_id"] = sid
    if scope:
        payload["scope"] = scope
    if memory_class:
        payload["memory_class"] = memory_class
    result = _eb_request("/memory/search", payload, timeout=timeout)
    if not isinstance(result, list):
        return []
    if include_audit:
        return result
    return [item for item in result if item.get("category") not in {"tool-call", "conversation", "todowrite"}]


def eb_search_global(
    query: str,
    *,
    max_results: int = 20,
    min_score: float = 0.0,
    session_key: str | None = None,
    memory_class: str | None = None,
    auto_recall: bool = False,
    timeout: float = 22.0,
) -> list[dict]:
    payload: dict[str, object] = {
        "query": query,
        "max_results": max_results,
        "min_score": min_score,
        "auto_recall": auto_recall,
        "scope": "global",
        "include_audit": False,
    }
    if memory_class:
        payload["memory_class"] = memory_class
    if session_key:
        payload["session_key"] = session_key
    result = _eb_request("/memory/search", payload, timeout=timeout)
    if not isinstance(result, list):
        return []
    return [item for item in result if item.get("category") not in {"tool-call", "conversation", "todowrite"}]


# ── session lifecycle ────────────────────────────────────────────────


def eb_session_start(
    session_key: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    gateway_short_name: str | None = None,
    parent_session_key: str | None = None,
    timeout: float = 12.0,
) -> dict | None:
    """Start a session via ``POST /sessions/start``.

    Replaces Cognee's ``/api/v1/agents/register``.
    """
    payload: dict[str, object] = {"session_key": session_key}
    if session_id:
        payload["session_id"] = session_id
    if agent_id:
        payload["agent_id"] = agent_id
    if gateway_short_name:
        payload["gateway_short_name"] = gateway_short_name
    if parent_session_key:
        payload["parent_session_key"] = parent_session_key
    return _eb_request("/sessions/start", payload, timeout=timeout)


def eb_session_end(
    session_key: str,
    *,
    session_id: str = "",
    reason: str = "session_end",
    agent_key: str | None = None,
    timeout: float = 12.0,
) -> dict | None:
    """End a session via ``POST /sessions/end``.

    Replaces Cognee's ``/api/v1/agents/unregister``.
    """
    payload: dict[str, object] = {"session_key": session_key, "session_id": session_id, "reason": reason}
    if agent_key:
        payload["agent_key"] = agent_key
    return _eb_request("/sessions/end", payload, timeout=timeout)


# ── ingest turn (primary write path in FULL mode) ──────────────────


def eb_ingest_turn(
    session_key: str,
    messages: list[dict],
    *,
    session_id: str | None = None,
    profile_name: str = "coding",
    timeout: float = 60.0,
) -> dict | None:
    """Ingest a conversation turn via ``POST /memory/ingest-turn``.

    ``/memory/ingest-messages`` is **gated in ElephantBroker FULL mode**
    (the ``IContextLifecycle`` branch returns 202/buffered without
    extracting anything).  ``/memory/ingest-turn`` is the correct entry
    point when the server is running with the context engine: it runs
    the full ``TurnIngestPipeline`` (extraction → embedding → facade
    store → Cognee cognify) immediately.

    ``session_id`` must be a valid UUID.  If the caller only has a
    string key, use ``_stable_uuid()`` to derive one deterministically.
    """
    payload: dict[str, object] = {
        "session_key": session_key,
        "messages": messages,
        "profile_name": profile_name,
    }
    if session_id:
        try:
            sid = str(uuid.UUID(session_id))
        except (ValueError, TypeError):
            sid = _stable_uuid(session_id)
    else:
        sid = _stable_uuid(session_key)
    payload["session_id"] = sid
    return _eb_request("/memory/ingest-turn", payload, timeout=timeout)


# ── ingest messages (batch) ──────────────────────────────────────────


def eb_ingest_messages(
    session_key: str,
    messages: list[dict],
    *,
    session_id: str | None = None,
    profile_name: str = "coding",
    timeout: float = 60.0,
) -> dict | None:
    """Ingest conversation messages via ``POST /memory/ingest-messages``.

    Replaces Cognee's cognify/remember batch path. EB's ingest pipeline
    extracts facts from the provided message list.
    """
    payload: dict[str, object] = {
        "session_key": session_key,
        "messages": messages,
        "profile_name": profile_name,
    }
    if session_id:
        payload["session_id"] = session_id
    return _eb_request("/memory/ingest-messages", payload, timeout=timeout)


def eb_remember_fact_or_fallback(
    text: str,
    *,
    session_key: str,
    session_id: str | None = None,
    category: str = "general",
) -> dict | None:
    """Store a fact directly, or fall back to ingest when direct store fails.

    This helper is for explicit fact writes, not interactive hook writes. Keep
    the direct-store deadline short because degraded embedding/vector paths can
    otherwise block for minutes before falling back.
    """
    try:
        result = eb_store_entry(
            text,
            category=category,
            session_key=session_key,
            session_id=session_id,
            timeout=8.0,
        )
        if isinstance(result, dict):
            return {"mode": "store", "result": result}
    except Exception as exc:
        _hook_log("store_failed_fallback_to_ingest", {"error": str(exc)[:200]})

    messages = [{"role": "user", "content": text}]
    ingest = eb_ingest_turn(session_key, messages, session_id=session_id, timeout=22.0)
    return {"mode": "ingest_turn_fallback", "result": ingest}


# ── synchronous persist (bridge equivalent) ──────────────────────────


def eb_persist_session(
    dataset: str,
    session_id: str,
    timeout: float = 300.0,
) -> PersistResult:
    """Bridge cached session QA/trace into EB via ingest-turn.

    ``/memory/ingest-turn`` is the correct entry point in FULL mode.
    ``/memory/ingest-messages`` is gated and silently skips extraction.
    ``/memory/store`` can fail when the embedding path is degraded.

    Returns a status-bearing result so callers can distinguish retryable
    infrastructure failures from legitimate empty/unchanged completion.
    """
    from _plugin_common import (
        _HTTP_BRIDGE_CACHE,
        _HTTP_BRIDGE_STATE,
        _bridge_cache_key,
        _load_json_file,
        sync_lock,
    )

    with sync_lock("eb-persist-session") as acquired:
        if not acquired:
            return PersistResult(PersistStatus.LOCK_BUSY)

        cache = _load_json_file(_HTTP_BRIDGE_CACHE)
        key = _bridge_cache_key(dataset, session_id)
        session_cache = cache.get(key, {}) if isinstance(cache, dict) else {}
        submitted_qa = list(session_cache.get("qa", []) or [])
        submitted_trace = list(session_cache.get("trace", []) or [])

        messages: list[dict[str, str]] = []
        for entry in submitted_qa:
            question = str(entry.get("question") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            if question:
                messages.append({"role": "user", "content": question})
            if answer:
                messages.append({"role": "assistant", "content": answer})
        for entry in submitted_trace:
            text = str(entry or "").strip()
            if text:
                messages.append({"role": "tool", "content": text})

        if not messages:
            state = _load_json_file(_HTTP_BRIDGE_STATE)
            state_key = f"{key}:eb_ingest_turn"
            status_value = PersistStatus.UNCHANGED if state.get(state_key) else PersistStatus.EMPTY
            return PersistResult(status_value)

        base_url = _service_url()
        if not base_url:
            return PersistResult(PersistStatus.HEALTH_FAILED)
        try:
            with urllib.request.urlopen(f"{base_url}/health/", timeout=2.0):
                pass
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            _hook_log("eb_persist_health_failed", {"error": str(exc)[:200]})
            return PersistResult(PersistStatus.HEALTH_FAILED)

        try:
            result = eb_ingest_turn(session_id, messages, session_id=session_id, timeout=timeout)
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            _hook_log("eb_persist_ingest_failed", {"error": str(exc)[:200]})
            return PersistResult(PersistStatus.INGEST_FAILED, len(messages))
        if not isinstance(result, dict) or result.get("facts_extracted") is None:
            return PersistResult(PersistStatus.BACKEND_REJECTED, len(messages))

        current = _load_json_file(_HTTP_BRIDGE_CACHE)
        current_session = current.get(key, {}) if isinstance(current, dict) else {}
        current_qa = list(current_session.get("qa", []) or [])
        current_trace = list(current_session.get("trace", []) or [])
        if current_qa[: len(submitted_qa)] != submitted_qa or current_trace[: len(submitted_trace)] != submitted_trace:
            return PersistResult(PersistStatus.ACK_FAILED, len(messages))
        current_session["qa"] = current_qa[len(submitted_qa) :]
        current_session["trace"] = current_trace[len(submitted_trace) :]
        current[key] = current_session
        try:
            _atomic_write_json(_HTTP_BRIDGE_CACHE, current)
        except OSError as exc:
            _hook_log("eb_persist_ack_failed", {"error": str(exc)[:200]})
            return PersistResult(PersistStatus.ACK_FAILED, len(messages))

        _hook_log(
            "eb_persist_result",
            {"dataset": dataset, "session_id": session_id, "message_count": len(messages)},
        )
        return PersistResult(PersistStatus.FLUSHED, len(messages))


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


# ── resolve runtime mode ─────────────────────────────────────────────


def eb_runtime_mode() -> dict:
    """Return a mode dict compatible with the plugin's ``resolve_runtime_mode()``."""
    url = _service_url()
    return {
        "mode": "eb",
        "service_url": url,
        "api_key_present": True,  # EB auth is a stub, so "present" is always true
        "url_source": "env_service_url",
        "key_source": "eb_mode",
    }
