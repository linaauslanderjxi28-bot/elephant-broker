from __future__ import annotations

import json
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from elephantbroker.schemas.procedure import ProcedureDefinition


PLUGIN_ROOT = Path(__file__).parent


def load_tools_module() -> ModuleType:
    path = PLUGIN_ROOT / "tools.py"
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("hermes_elephantbroker_scope_tools", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Hermes tools module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProvider:
    name: str = "test"
    _session_key: str = "current-session"
    _session_id: str = "00000000-0000-4000-8000-000000000001"
    _profile_name: str = "coding"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []
        self.results: list[dict[str, object]] = []
        self.errors: dict[str, OSError] = {}

    def _eb_request(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        method: str = "POST",
        timeout: float = 30.0,
    ) -> list[dict[str, object]] | dict[str, object] | None:
        self.calls.append((path, payload or {}, {"method": method, "timeout": timeout}))
        if path in self.errors:
            raise self.errors[path]
        return self.results


class TestScopeTools(unittest.TestCase):
    def test_search_omits_session_identity_when_scope_is_omitted(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {"query": "probe"})

        payload = provider.calls[0][1]
        self.assertNotIn("scope", payload)
        self.assertNotIn("session_key", payload)
        self.assertNotIn("session_id", payload)

    def test_search_includes_session_identity_for_session_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {"query": "probe", "scope": "session"})

        payload = provider.calls[0][1]
        self.assertEqual(payload["scope"], "session")
        self.assertEqual(payload["session_key"], "current-session")
        self.assertEqual(payload["session_id"], "00000000-0000-4000-8000-000000000001")

    def test_search_omits_session_identity_for_team_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {"query": "probe", "scope": "team"})

        payload = provider.calls[0][1]
        self.assertEqual(payload["scope"], "team")
        self.assertNotIn("session_key", payload)
        self.assertNotIn("session_id", payload)

    def test_search_omits_session_identity_for_organization_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {"query": "probe", "scope": "organization"})

        payload = provider.calls[0][1]
        self.assertEqual(payload["scope"], "organization")
        self.assertNotIn("session_key", payload)
        self.assertNotIn("session_id", payload)

    def test_search_omits_session_identity_for_global_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {"query": "probe", "scope": "global"})

        payload = provider.calls[0][1]
        self.assertEqual(payload["scope"], "global")
        self.assertNotIn("session_key", payload)
        self.assertNotIn("session_id", payload)

    def test_search_global_sends_global_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search_global(provider, {"query": "probe"})

        path, payload, _options = provider.calls[0]
        self.assertEqual(path, "/memory/search")
        self.assertEqual(payload["scope"], "global")
        self.assertNotIn("session_key", payload)
        self.assertNotIn("session_id", payload)

    def test_search_global_timeout_returns_degraded_status(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()
        provider.errors["/memory/search"] = TimeoutError("timed out")

        output = tools.handle_search_global(provider, {"query": "probe"})

        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "degraded")
        self.assertEqual(parsed["reason"], "timeout")
        self.assertIn("Global search timed out", parsed["message"])

    def test_store_uses_explicit_team_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_store(provider, {"text": "shared fact", "scope": "team"})

        fact = provider.calls[0][1]["fact"]
        if not isinstance(fact, dict):
            self.fail("store payload fact must be a dict")
        self.assertEqual(fact["scope"], "team")

    def test_store_uses_explicit_organization_scope(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_store(provider, {"text": "shared fact", "scope": "organization"})

        fact = provider.calls[0][1]["fact"]
        if not isinstance(fact, dict):
            self.fail("store payload fact must be a dict")
        self.assertEqual(fact["scope"], "organization")

    def test_invalid_scope_returns_error_without_request(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        output = tools.handle_search(provider, {"query": "probe", "scope": "workspace"})

        self.assertEqual(provider.calls, [])
        self.assertIn("Invalid scope", json.loads(output)["error"])

    def test_store_accepts_structured_fields(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_store(provider, {
            "text": "structured fact",
            "memory_class": "semantic",
            "confidence": 0.7,
            "decision_domain": "testing",
            "target_actor_ids": ["00000000-0000-4000-8000-000000000002"],
            "autorecall_blacklisted": True,
        })

        payload = provider.calls[0][1]
        fact = payload["fact"]
        if not isinstance(fact, dict):
            self.fail("store payload fact must be a dict")
        self.assertEqual(fact["memory_class"], "semantic")
        self.assertEqual(fact["confidence"], 0.7)
        self.assertEqual(fact["decision_domain"], "testing")
        self.assertEqual(fact["target_actor_ids"], ["00000000-0000-4000-8000-000000000002"])
        self.assertEqual(fact["autorecall_blacklisted"], True)

    def test_store_preserves_research_decision_identity(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_store(provider, {
            "text": "decision text",
            "category": "decision",
            "memory_class": "semantic",
            "entity_type": "ResearchDecision",
            "entity_name": "decision text",
            "decision_status": "actioned",
            "scope": "team",
        })

        payload = provider.calls[0][1]
        fact = payload["fact"]
        if not isinstance(fact, dict):
            self.fail("store payload fact must be a dict")
        self.assertEqual(fact["category"], "decision")
        self.assertEqual(fact["scope"], "team")
        self.assertEqual(fact["memory_class"], "semantic")
        self.assertEqual(fact["entity_type"], "ResearchDecision")
        self.assertEqual(fact["entity_name"], "decision text")
        self.assertEqual(fact["decision_status"], "actioned")

    def test_store_503_returns_retryable_degraded_status(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()
        provider.errors["/memory/store"] = OSError("HTTP Error 503: Service Unavailable")

        output = tools.handle_store(provider, {"text": "retry me"})

        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "degraded")
        self.assertEqual(parsed["reason"], "store_unavailable")
        self.assertEqual(parsed["retryable"], True)

    def test_search_accepts_backend_filters(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_search(provider, {
            "query": "probe",
            "min_score": 0.42,
            "memory_class": "semantic",
            "session_key": "external-session",
            "profile_name": "research",
        })

        payload = provider.calls[0][1]
        self.assertEqual(payload["min_score"], 0.42)
        self.assertEqual(payload["memory_class"], "semantic")
        self.assertEqual(payload["session_key"], "external-session")
        self.assertEqual(payload["profile_name"], "research")

    def test_memory_get_uses_get_endpoint(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_get", {"fact_id": "fact-1"})

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/memory/fact-1")
        self.assertEqual(payload, {})
        self.assertEqual(options["method"], "GET")

    def test_memory_update_uses_patch_endpoint(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_update", {
            "fact_id": "fact-1",
            "text": "new text",
            "category": "updated",
        })

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/memory/fact-1")
        self.assertEqual(payload["text"], "new text")
        self.assertEqual(payload["category"], "updated")
        self.assertEqual(options["method"], "PATCH")

    def test_memory_forget_uses_delete_endpoint(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_forget", {"fact_id": "fact-1"})

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/memory/fact-1")
        self.assertEqual(payload, {})
        self.assertEqual(options["method"], "DELETE")

    def test_session_goal_create_uses_session_endpoint(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_goal_create", {"title": "Ship plugin"})

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/goals/session?session_key=current-session&session_id=00000000-0000-4000-8000-000000000001")
        self.assertEqual(payload["title"], "Ship plugin")
        self.assertEqual(options["method"], "POST")

    def test_procedure_activate_adds_session_context(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_procedure_activate", {"procedure_id": "proc-1"})

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/procedures/proc-1/activate")
        self.assertEqual(payload["session_key"], "current-session")
        self.assertEqual(payload["session_id"], "00000000-0000-4000-8000-000000000001")
        self.assertEqual(payload["profile_name"], "coding")
        self.assertEqual(options["method"], "POST")

    def test_procedure_create_normalizes_string_steps_and_activation_modes(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_procedure_create", {
            "name": "Self test procedure",
            "steps": ["Run first check", "Run second check"],
            "activation_modes": ["manual"],
        })

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/procedures/")
        self.assertEqual(payload["steps"], [
            {"order": 0, "instruction": "Run first check"},
            {"order": 1, "instruction": "Run second check"},
        ])
        self.assertEqual(payload["activation_modes"], [{"manual": True}])
        self.assertEqual(payload["is_manual_only"], False)
        self.assertEqual(options["method"], "POST")

    def test_procedure_create_payload_validates_against_backend_schema(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_procedure_create", {
            "name": "Self test procedure",
            "steps": [{"instruction": "Run first check"}],
            "activation_modes": [{"manual": True}],
        })

        payload = provider.calls[0][1]
        ProcedureDefinition.model_validate(payload)

    def test_artifact_create_defaults_to_current_session(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_artifact_create", {
            "tool_name": "pytest",
            "content": "all green",
        })

        path, payload, options = provider.calls[0]
        self.assertEqual(path, "/artifacts/create")
        self.assertEqual(payload["scope"], "session")
        self.assertEqual(payload["session_key"], "current-session")
        self.assertEqual(payload["session_id"], "00000000-0000-4000-8000-000000000001")
        self.assertEqual(options["method"], "POST")

    def test_artifact_create_uses_content_as_session_summary_when_summary_missing(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        _ = tools.handle_tool_call(provider, "elephantbroker_artifact_create", {
            "tool_name": "pytest",
            "content": "unique self test artifact output",
        })

        payload = provider.calls[0][1]
        self.assertEqual(payload["summary"], "unique self test artifact output")

    def test_artifact_search_session_reads_artifact_id_directly(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()
        artifact_id = "11111111-1111-4111-8111-111111111111"

        _ = tools.handle_artifact_search(provider, {"query": artifact_id, "scope": "session"})

        path, payload, options = provider.calls[0]
        self.assertEqual(path, f"/artifacts/session/{artifact_id}?session_key=current-session&session_id=00000000-0000-4000-8000-000000000001")
        self.assertEqual(payload, {})
        self.assertEqual(options["method"], "GET")

    def test_actor_inspect_optionally_loads_relationships_and_authority(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()
        actor_id = "00000000-0000-4000-8000-000000000002"

        _ = tools.handle_tool_call(provider, "elephantbroker_actor_inspect", {
            "actor_id": actor_id,
            "include_relationships": True,
            "include_authority_chain": True,
        })

        self.assertEqual(provider.calls[0][0], f"/actors/{actor_id}")
        self.assertEqual(provider.calls[1][0], f"/actors/{actor_id}/relationships")
        self.assertEqual(provider.calls[2][0], f"/actors/{actor_id}/authority-chain")

    def test_actor_inspect_rejects_non_uuid_without_backend_422(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()

        output = tools.handle_actor_inspect(provider, {"actor_id": "not-a-uuid"})

        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "unavailable")
        self.assertEqual(parsed["reason"], "invalid_actor_id")
        self.assertEqual(provider.calls, [])

    def test_guards_list_404_returns_optional_module_status(self) -> None:
        tools = load_tools_module()
        provider = FakeProvider()
        provider.errors["/guards/active/00000000-0000-4000-8000-000000000001"] = OSError("HTTP Error 404: Not Found")

        output = tools.handle_guards_list(provider, {})

        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "unavailable")
        self.assertEqual(parsed["reason"], "guards_unavailable")
        self.assertIn("refresh", parsed["message"])


if __name__ == "__main__":
    _ = unittest.main()
