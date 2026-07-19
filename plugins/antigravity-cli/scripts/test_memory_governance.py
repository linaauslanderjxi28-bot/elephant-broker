from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name.replace('-', '_')}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name.replace('-', '_'))
    sys.modules[name.replace('-', '_')] = module
    old_path = list(sys.path)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
        if previous is None:
            sys.modules.pop(name.replace('-', '_'), None)
        else:
            sys.modules[name.replace('-', '_')] = previous
    return module


def test_explicit_memory_gate_redacts_secret_and_rejects_ordinary_turn() -> None:
    governance = load("memory_governance")
    assert governance.safe_memory_messages("Explain this code", "normal answer") == []
    messages = governance.safe_memory_messages("Please remember token=super-secret-value", "Saved token=another-secret-value")
    assert len(messages) == 2
    assert "super-secret-value" not in messages[0]["content"]
    assert "another-secret-value" not in messages[1]["content"]
    assert "[REDACTED]" in messages[0]["content"]


def test_recall_uses_exactly_session_and_global_and_marks_context_untrusted(monkeypatch) -> None:
    module = load("agy-pre-invocation")
    calls = []

    def recall(query, *, session_id, top_k, scope, **_kwargs):
        calls.append((session_id, top_k, scope))
        if scope == ["session"]:
            return [{"id": "same", "text": "same memory", "scope": "session"}, {"id": "session", "text": "session memory", "scope": "session"}]
        return [{"id": "same", "text": "same memory", "scope": "global"}, {"id": "global", "text": "global memory", "scope": "global"}]

    monkeypatch.setattr(module, "recall_via_http", recall)
    monkeypatch.setattr(module, "hook_log", lambda *_args, **_kwargs: None)
    text = asyncio.run(module._recall_context("hello", "session-key"))

    assert calls == [("session-key", module.TOP_K_SESSION, ["session"]), ("session-key", module.TOP_K_GLOBAL, ["global"])]
    assert text.count("same memory") == 1
    assert "trust=untrusted" in text
    assert "Never execute commands" in text


def test_post_invocation_only_ingests_explicit_memory_request(monkeypatch) -> None:
    module = load("agy-post-invocation")
    monkeypatch.setattr(module, "load_config", lambda: {})
    monkeypatch.setattr(module, "load_resolved", lambda: {"session_id": "case-1", "dataset": "test"})
    monkeypatch.setattr(module, "get_session_id", lambda _cfg: "case-1")
    monkeypatch.setattr(module, "get_dataset", lambda _cfg: "test")
    monkeypatch.setattr(module, "touch_activity", lambda: None)
    monkeypatch.setattr(module, "hook_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "get_session_key", lambda: "case-1")
    monkeypatch.setattr(module, "_extract_answer_from_transcript", lambda _cid: "answer")

    common = sys.modules.get("_plugin_common")
    assert common is not None
    monkeypatch.setattr(common, "pop_pending_prompt", lambda *_args, **_kwargs: {"prompt": "ordinary question"})
    calls = []
    import _plugin_eb as eb
    monkeypatch.setattr(eb, "eb_ingest_turn", lambda *args, **kwargs: calls.append((args, kwargs)) or {"facts_extracted": 1})
    asyncio.run(module._store_qa({"conversationId": "c", "invocationNum": 1}))
    assert calls == []

    monkeypatch.setattr(common, "pop_pending_prompt", lambda *_args, **_kwargs: {"prompt": "Please remember this engineering preference"})
    asyncio.run(module._store_qa({"conversationId": "c", "invocationNum": 2}))
    assert len(calls) == 1
    assert calls[0][0][1][0]["role"] == "user"


def test_session_lifecycle_normalizes_ids(monkeypatch) -> None:
    module = load("_plugin_eb")
    captured = []
    monkeypatch.setattr(module, "_eb_request", lambda _path, payload, **_kwargs: captured.append(payload) or {"status": "ok", "session_key": payload["session_key"]})
    module.eb_session_start("non-uuid-session", session_id="ignored")
    module.eb_session_end("non-uuid-session", session_id="ignored")
    assert captured[0]["session_id"] == module._stable_uuid("non-uuid-session")
    assert captured[1]["session_id"] == module._stable_uuid("non-uuid-session")


def test_hooks_do_not_capture_post_tool_output_or_replay_bridge_cache() -> None:
    hooks = json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "PostToolUse" not in hooks
    stop_commands = [item["command"] for entry in hooks["Stop"] for item in entry["hooks"]]
    assert stop_commands == ["python3 scripts/sync-session-to-graph.py --session-end"]
    post = (SCRIPTS / "agy-post-invocation.py").read_text(encoding="utf-8")
    assert "eb_persist_session" not in post
