from __future__ import annotations

import json
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


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

    def _eb_request(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        method: str = "POST",
        timeout: float = 30.0,
    ) -> list[dict[str, object]]:
        self.calls.append((path, payload or {}, {"method": method, "timeout": timeout}))
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


if __name__ == "__main__":
    _ = unittest.main()
