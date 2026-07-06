from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent


def load_local_module(name: str) -> ModuleType:
    module_name = f"elephantbroker_hermes_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    module_path = PLUGIN_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load local module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_client_module = load_local_module("client")
_compat_module = load_local_module("compat")
_config_module = load_local_module("config")
_schemas_module = load_local_module("schemas")
_tools_module = load_local_module("tools")
_utils_module = load_local_module("utils")
_writer_module = load_local_module("writer")

ElephantBrokerClient = _client_module.ElephantBrokerClient
MemoryProvider = _compat_module.MemoryProvider
SEARCH_GLOBAL_SCHEMA = _schemas_module.SEARCH_GLOBAL_SCHEMA
SEARCH_SCHEMA = _schemas_module.SEARCH_SCHEMA
STORE_SCHEMA = _schemas_module.STORE_SCHEMA
WriteQueue = _writer_module.WriteQueue
config_schema = _config_module.config_schema
load_config = _config_module.load_config
save_config = _config_module.save_config
stable_uuid = _utils_module.stable_uuid

logger = logging.getLogger(__name__)


class ElephantBrokerMemoryProvider(MemoryProvider):
    """ElephantBroker memory provider plugin for NousResearch Hermes Agent."""

    def __init__(self) -> None:
        super().__init__()
        self._config: dict[str, str] = {}
        self._client = ElephantBrokerClient("http://localhost:8420", "", "")
        self._service_url = ""
        self._gateway_id = ""
        self._agent_key = ""
        self._profile_name = ""
        self._session_key = ""
        self._session_id = ""
        self._agent_context = "primary"
        self._active = False
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._sync_thread = None
        self._writer = WriteQueue()

    @property
    def name(self) -> str:
        return "elephantbroker"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._config = load_config()
        self._service_url = self._config.get("service_url", "http://localhost:8420").rstrip("/")
        self._gateway_id = self._config.get("gateway_id", "")
        self._agent_key = self._config.get("agent_key", "")
        self._profile_name = self._config.get("profile_name", "coding")
        self._active = bool(self._gateway_id)
        if not self._active:
            logger.warning("ElephantBroker gateway_id not configured; memory provider inactive")
        self._client = ElephantBrokerClient(self._service_url, self._gateway_id, self._agent_key)
        self._session_key = session_id
        self._session_id = stable_uuid(session_id)
        self._agent_context = kwargs.get("agent_context", "primary")

        if self._agent_context != "primary" or not self._active:
            return

        parent_session_key = kwargs.get("parent_session_id")
        agent_id = kwargs.get("agent_identity") or kwargs.get("agent_workspace") or "hermes-agent"

        def register_session() -> None:
            payload = {"session_key": self._session_key, "session_id": self._session_id, "agent_id": agent_id}
            if parent_session_key:
                payload["parent_session_key"] = parent_session_key
            try:
                self._eb_request("/sessions/start", payload, timeout=5.0)
            except Exception as e:
                logger.warning("ElephantBroker session start failed: %s", e)

        threading.Thread(target=register_session, daemon=True, name="eb-session-start").start()

    def _default_headers(self) -> dict[str, str]:
        return self._client.default_headers()

    def _eb_request(self, path: str, payload: dict[str, Any] | None = None, *, method: str = "POST", timeout: float = 30.0) -> Any:
        return self._client.request(path, payload, method=method, timeout=timeout)

    def _enqueue_write(self, work) -> None:
        self._writer.enqueue(work)

    def flush(self, timeout: float = 5.0) -> bool:
        return self._writer.flush(timeout)

    @property
    def _writer_thread(self):
        return self._writer.thread

    def system_prompt_block(self) -> str:
        return (
            "# ElephantBroker Memory\n"
            f"Active. Session Key: {self._session_key}.\n"
            "Use elephantbroker_search to look up user facts, preferences, and details. "
            "Use elephantbroker_store to record explicit facts."
        )

    def _format_search_results(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return ""
        lines: list[str] = []
        for result in results:
            text = result.get("text", "")
            if "question" in result or "answer" in result:
                lines.append(f"- Q: {result.get('question', '')}\n  A: {result.get('answer', '')}")
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
        if self._agent_context != "primary" or not self._active:
            return

        def run() -> None:
            try:
                sid = stable_uuid(session_id) if session_id else self._session_id
                skey = session_id or self._session_key
                # Session scope — personal conversation context
                session_payload = {"query": query, "max_results": 5, "session_key": skey, "session_id": sid, "auto_recall": True}
                session_results = self._eb_request("/memory/search", session_payload, timeout=10.0)
                if not isinstance(session_results, list):
                    session_results = []
                # Global scope — scrapling/doc-ingestor imported data
                global_payload = {"query": query, "max_results": 5, "scope": "global", "profile_name": self._profile_name}
                global_results = self._eb_request("/memory/search", global_payload, timeout=10.0)
                if not isinstance(global_results, list):
                    global_results = []
                all_results = session_results + global_results
                if all_results:
                    formatted = self._format_search_results(all_results)
                    with self._prefetch_lock:
                        self._prefetch_result = formatted
            except Exception as e:
                logger.debug("ElephantBroker prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=run, daemon=True, name="eb-prefetch")
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", messages: list[dict[str, Any]] | None = None) -> None:
        if self._agent_context != "primary" or not self._active:
            return
        turn_messages = [dict(message) for message in messages] if messages is not None else [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]

        def sync() -> None:
            try:
                skey = session_id or self._session_key
                sid = stable_uuid(session_id) if session_id else self._session_id
                payload = {"session_key": skey, "session_id": sid, "profile_name": self._profile_name, "messages": turn_messages}
                self._eb_request("/memory/ingest-turn", payload, timeout=60.0)
            except Exception as e:
                logger.warning("ElephantBroker sync_turn failed: %s", e)

        self._enqueue_write(sync)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [SEARCH_SCHEMA, SEARCH_GLOBAL_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        return _tools_module.handle_tool_call(self, tool_name, args)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, rewound: bool = False, **kwargs: Any) -> None:
        self._session_key = new_session_id
        self._session_id = stable_uuid(new_session_id)
        if reset and self._agent_context == "primary" and self._active:
            def register_session() -> None:
                payload = {"session_key": self._session_key, "session_id": self._session_id, "agent_id": "hermes-agent"}
                if parent_session_id:
                    payload["parent_session_key"] = parent_session_id
                try:
                    self._eb_request("/sessions/start", payload, timeout=5.0)
                except Exception as e:
                    logger.warning("ElephantBroker session switch start failed: %s", e)
            threading.Thread(target=register_session, daemon=True, name="eb-session-switch-start").start()

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if self._agent_context != "primary" or not self._active:
            return

        def write() -> None:
            payload = {"session_key": self._session_key, "session_id": self._session_id, "reason": "session_end"}
            self._eb_request("/sessions/end", payload, timeout=3.0)

        self._enqueue_write(write)
        if not self.flush(timeout=5.0):
            logger.warning("ElephantBroker session end flush timed out")

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        if self._agent_context != "primary" or not self._active or action not in ("add", "replace"):
            return
        payload = {
            "fact": {"text": content, "category": target, "scope": "session", "memory_class": "episodic", "confidence": 1.0},
            "session_key": self._session_key,
            "session_id": self._session_id,
        }
        self._enqueue_write(lambda: self._eb_request("/memory/store", payload, timeout=10.0))

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        if self._agent_context != "primary" or not self._active or not messages:
            return ""
        compress_messages = [dict(message) for message in messages]
        payload = {"session_key": self._session_key, "session_id": self._session_id, "profile_name": self._profile_name, "messages": compress_messages, "source": "pre_compress"}
        self._enqueue_write(lambda: self._eb_request("/memory/ingest-turn", payload, timeout=60.0))
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs: Any) -> None:
        if self._agent_context != "primary" or not self._active:
            return
        fact_text = f"Delegated task: '{task}' -> Result: '{result}'"
        payload = {
            "fact": {"text": fact_text, "category": "delegation", "scope": "session", "memory_class": "episodic", "confidence": 1.0},
            "session_key": self._session_key,
            "session_id": self._session_id,
        }
        self._enqueue_write(lambda: self._eb_request("/memory/store", payload, timeout=10.0))

    def get_config_schema(self) -> list[dict[str, Any]]:
        return config_schema()

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        save_config(values, hermes_home)

    def shutdown(self) -> None:
        if not self._writer.shutdown(flush_timeout=5.0, join_timeout=3.0):
            logger.warning("ElephantBroker shutdown flush timed out")
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
