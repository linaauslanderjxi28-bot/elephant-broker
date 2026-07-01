"""ElephantBroker memory plugin for NousResearch Hermes Agent.

Provides persistent memory and session tracking by integration with the
ElephantBroker Gateway and context engine.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    class MemoryProvider:
        pass

logger = logging.getLogger(__name__)

# Fallback imports to support older versions or different environments
try:
    from tools.registry import tool_error
except ImportError:
    def tool_error(msg: str) -> str:
        return json.dumps({"error": msg})

try:
    from hermes_constants import get_hermes_home
except ImportError:
    from pathlib import Path
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()

try:
    from utils import atomic_json_write
except ImportError:
    from pathlib import Path
    def atomic_json_write(path, data, mode=0o600):
        import tempfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False, encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            temp_name = f.name
        os.chmod(temp_name, mode)
        os.replace(temp_name, str(path))


def _stable_uuid(text: str) -> str:
    """Derive a deterministic UUID from arbitrary text."""
    if not text:
        return str(uuid.UUID(int=0))
    try:
        return str(uuid.UUID(text))
    except (ValueError, TypeError):
        pass
    return str(uuid.UUID(hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]))


def _load_config() -> dict:
    """Load ElephantBroker config from environment or JSON file."""
    hermes_home = get_hermes_home()

    config = {
        "service_url": os.environ.get("EB_SERVICE_URL") or os.environ.get("COGNEE_SERVICE_URL") or "http://localhost:8420",
        "gateway_id": os.environ.get("EB_GATEWAY_ID", "gw-enterprise-prod"),
        "agent_key": os.environ.get("EB_AGENT_KEY", ""),
        "profile_name": os.environ.get("EB_PROFILE_NAME", "coding"),
    }

    config_path = hermes_home / "elephantbroker.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items()
                           if v is not None and v != ""})
        except Exception:
            pass

    return config


# ── Tool Schemas ──────────────────────────────────────────────────────

SEARCH_SCHEMA = {
    "name": "elephantbroker_search",
    "description": (
        "Search ElephantBroker long-term memories by semantic meaning. "
        "Returns relevant facts, user preferences, and prior conversation QA."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "Max results to return (default: 5, max: 20)."},
            "entity_type": {"type": "string", "description": "Entity type filter: FinancialReport, Invoice, Contract, Document"},
        },
        "required": ["query"],
    },
}

SEARCH_GLOBAL_SCHEMA = {
    "name": "elephantbroker_search_global",
    "description": (
        "Search the global ElephantBroker knowledge base. Use this for data imported from scrapling, doc-ingestor, or other non-session pipelines."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The global search query."},
            "max_results": {"type": "integer", "description": "Max global results to return (default: 10, max: 20)."},
            "session_key": {"type": "string", "description": "Optional global session key filter, e.g. scrapling:example-com or doc-ingestor:0-inbox."},
            "entity_type": {"type": "string", "description": "Entity type filter: FinancialReport, Invoice, Contract, Document"},
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "elephantbroker_store",
    "description": (
        "Store a durable, explicit fact in ElephantBroker memory. "
        "Use this to persist corrections, key user decisions, or lasting preferences."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact text to store."},
            "category": {"type": "string", "description": "Optional category label (default: 'general')."},
            "entity_type": {"type": "string", "description": "Entity type: FinancialReport, Invoice, Contract, Document"},
            "decision_status": {"type": "string", "description": "Decision status: proposed, approved, rejected, actioned"},
            "goal_ids": {"type": "array", "items": {"type": "string"}, "description": "Fact IDs this fact relates to"},
        },
        "required": ["text"],
    },
}


class ElephantBrokerMemoryProvider(MemoryProvider):
    """ElephantBroker memory provider plugin for NousResearch Hermes Agent."""

    def __init__(self):
        self._config = None
        self._service_url = ""
        self._gateway_id = ""
        self._agent_key = ""
        self._profile_name = ""
        
        self._session_key = ""
        self._session_id = ""
        self._agent_context = "primary"

        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread = None
        self._sync_thread = None

    @property
    def name(self) -> str:
        return "elephantbroker"

    def is_available(self) -> bool:
        # Defaults to local gateway, always available for configuration and use.
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._service_url = self._config.get("service_url", "http://localhost:8420").rstrip("/")
        self._gateway_id = self._config.get("gateway_id", "gw-enterprise-prod")
        self._agent_key = self._config.get("agent_key", "")
        self._profile_name = self._config.get("profile_name", "coding")

        self._session_key = session_id
        self._session_id = _stable_uuid(session_id)
        self._agent_context = kwargs.get("agent_context", "primary")

        # Skip registration for non-primary contexts (e.g. subagents, cron)
        if self._agent_context != "primary":
            return

        parent_session_key = kwargs.get("parent_session_id")
        agent_id = kwargs.get("agent_identity") or kwargs.get("agent_workspace") or "hermes-agent"

        # Register session via POST /sessions/start
        def register_session():
            payload = {
                "session_key": self._session_key,
                "session_id": self._session_id,
                "agent_id": agent_id,
            }
            if parent_session_key:
                payload["parent_session_key"] = parent_session_key
            try:
                self._eb_request("/sessions/start", payload, timeout=5.0)
            except Exception:
                pass

        threading.Thread(target=register_session, daemon=True, name="eb-session-start").start()

    def _default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._gateway_id:
            headers["X-EB-Gateway-ID"] = self._gateway_id
        if self._agent_key:
            headers["X-EB-Agent-Key"] = self._agent_key
        actor_id = os.environ.get("EB_ACTOR_ID", "").strip()
        if actor_id:
            headers["X-EB-Actor-Id"] = actor_id
        auth_token = os.environ.get("EB_AUTH_TOKEN", "").strip()
        if auth_token:
            headers["X-EB-Auth-Token"] = auth_token
        return headers

    def _eb_request(self, path: str, payload: dict | None = None, *, method: str = "POST", timeout: float = 30.0) -> Any:
        url = f"{self._service_url}{path}"
        headers = self._default_headers()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)

    def system_prompt_block(self) -> str:
        return (
            "# ElephantBroker Memory\n"
            f"Active. Session Key: {self._session_key}.\n"
            "Use elephantbroker_search to look up user facts, preferences, and details. "
            "Use elephantbroker_store to record explicit facts."
        )

    def _format_search_results(self, results: list[dict]) -> str:
        if not results:
            return ""
        lines = []
        for r in results:
            if not isinstance(r, dict):
                continue
            text = r.get("text", "")
            if "question" in r or "answer" in r:
                q = r.get("question", "")
                a = r.get("answer", "")
                lines.append(f"- Q: {q}\n  A: {a}")
            elif text:
                lines.append(f"- {text}")
        return "\n".join(lines)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=2.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## ElephantBroker Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._agent_context != "primary":
            return

        def _run():
            try:
                sid = _stable_uuid(session_id) if session_id else self._session_id
                skey = session_id or self._session_key
                payload = {
                    "query": query,
                    "max_results": 5,
                    "session_key": skey,
                    "session_id": sid,
                    "auto_recall": True
                }
                results = self._eb_request("/memory/search", payload, timeout=10.0)
                if isinstance(results, list) and results:
                    formatted = self._format_search_results(results)
                    with self._prefetch_lock:
                        self._prefetch_result = formatted
            except Exception as e:
                logger.debug("ElephantBroker prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="eb-prefetch")
        self._prefetch_thread.start()

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if self._agent_context != "primary":
            return

        def _sync():
            try:
                skey = session_id or self._session_key
                sid = _stable_uuid(session_id) if session_id else self._session_id
                turn_messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ]
                payload = {
                    "session_key": skey,
                    "session_id": sid,
                    "profile_name": self._profile_name,
                    "messages": turn_messages,
                }
                self._eb_request("/memory/ingest-turn", payload, timeout=60.0)
            except Exception as e:
                logger.warning("ElephantBroker sync_turn failed: %s", e)

        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="eb-sync-turn")
        self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, SEARCH_GLOBAL_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "elephantbroker_search":
            query = args.get("query", "")
            if not query:
                return json.dumps({"error": "Missing required parameter: query"})
            max_results = min(int(args.get("max_results", 5)), 20)
            
            payload = {
                "query": query,
                "max_results": max_results,
                "session_key": self._session_key,
                "session_id": self._session_id,
                "auto_recall": True
            }
            entity_type = args.get("entity_type")
            if entity_type:
                payload["entity_type"] = entity_type
            try:
                results = self._eb_request("/memory/search", payload, timeout=10.0)
                if not results:
                    return json.dumps({"result": "No matching memories found."})
                return json.dumps({"results": results, "count": len(results)})
            except Exception as e:
                return json.dumps({"error": f"Search failed: {e}"})

        elif tool_name == "elephantbroker_search_global":
            query = args.get("query", "")
            if not query:
                return json.dumps({"error": "Missing required parameter: query"})
            max_results = min(int(args.get("max_results", 10)), 20)
            payload = {
                "query": query,
                "max_results": max_results,
                "scope": "global",
            }
            session_key = args.get("session_key")
            if session_key:
                payload["session_key"] = session_key
            entity_type = args.get("entity_type")
            if entity_type:
                payload["entity_type"] = entity_type
            if self._profile_name:
                payload["profile_name"] = self._profile_name
            try:
                results = self._eb_request("/memory/search", payload, timeout=15.0)
                if not results:
                    return json.dumps({"result": "No matching global memories found."})
                return json.dumps({"results": results, "count": len(results)})
            except Exception as e:
                return json.dumps({"error": f"Global search failed: {e}"})

        elif tool_name == "elephantbroker_store":
            text = args.get("text", "")
            if not text:
                return json.dumps({"error": "Missing required parameter: text"})
            category = args.get("category", "general")
            
            fact = {
                "text": text,
                "category": category,
                "scope": "session",
                "memory_class": "episodic",
                "confidence": 1.0,
            }
            if args.get("entity_type"):
                fact["entity_type"] = args["entity_type"]
            if args.get("decision_status"):
                fact["decision_status"] = args["decision_status"]
            payload = {
                "fact": fact,
                "session_key": self._session_key,
                "session_id": self._session_id,
            }
            if args.get("goal_ids"):
                payload["goal_ids"] = args["goal_ids"]
            try:
                res = self._eb_request("/memory/store", payload, timeout=10.0)
                return json.dumps({"result": "Fact stored successfully.", "details": res})
            except Exception as e:
                return json.dumps({"error": f"Failed to store fact: {e}"})

        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        self._session_key = new_session_id
        self._session_id = _stable_uuid(new_session_id)
        
        if reset and self._agent_context == "primary":
            def register_session():
                payload = {
                    "session_key": self._session_key,
                    "session_id": self._session_id,
                    "agent_id": "hermes-agent",
                }
                if parent_session_id:
                    payload["parent_session_key"] = parent_session_id
                try:
                    self._eb_request("/sessions/start", payload, timeout=5.0)
                except Exception:
                    pass
            threading.Thread(target=register_session, daemon=True, name="eb-session-switch-start").start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._agent_context != "primary":
            return
            
        payload = {
            "session_key": self._session_key,
            "session_id": self._session_id,
            "reason": "session_end",
        }
        try:
            self._eb_request("/sessions/end", payload, timeout=3.0)
        except Exception:
            pass

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._agent_context != "primary":
            return
        if action in ("add", "replace"):
            fact = {
                "text": content,
                "category": target,
                "scope": "session",
                "memory_class": "episodic",
                "confidence": 1.0,
            }
            payload = {
                "fact": fact,
                "session_key": self._session_key,
                "session_id": self._session_id,
            }
            def _write():
                try:
                    self._eb_request("/memory/store", payload, timeout=10.0)
                except Exception:
                    pass
            threading.Thread(target=_write, daemon=True, name="eb-memory-write").start()

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        if self._agent_context != "primary":
            return
        fact_text = f"Delegated task: '{task}' -> Result: '{result}'"
        fact = {
            "text": fact_text,
            "category": "delegation",
            "scope": "session",
            "memory_class": "episodic",
            "confidence": 1.0,
        }
        payload = {
            "fact": fact,
            "session_key": self._session_key,
            "session_id": self._session_id,
        }
        def _write():
            try:
                self._eb_request("/memory/store", payload, timeout=10.0)
            except Exception:
                pass
        threading.Thread(target=_write, daemon=True, name="eb-delegation-write").start()

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "service_url", "description": "ElephantBroker service URL", "default": "http://localhost:8420", "env_var": "EB_SERVICE_URL"},
            {"key": "gateway_id", "description": "ElephantBroker Gateway ID", "default": "gw-enterprise-prod", "env_var": "EB_GATEWAY_ID"},
            {"key": "agent_key", "description": "ElephantBroker Agent Key", "secret": True, "env_var": "EB_AGENT_KEY"},
            {"key": "profile_name", "description": "Context engine profile name", "default": "coding"},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "elephantbroker.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(values)
        atomic_json_write(config_path, existing, mode=0o600)

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=3.0)


def register(ctx) -> None:
    ctx.register_memory_provider(ElephantBrokerMemoryProvider())
