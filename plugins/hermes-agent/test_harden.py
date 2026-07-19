from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_DIR = Path(__file__).parent


def load_plugin_module():
    path = PLUGIN_DIR / "__init__.py"
    module_name = f"hermes_elephantbroker_plugin_harden_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Hermes plugin")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProviderTestCase(unittest.TestCase):
    def provider(self):
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._agent_context = "primary"
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        return module, provider


class TestConfigFallbacks(unittest.TestCase):
    def test_load_config_prefers_eb_service_url(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"EB_SERVICE_URL": "http://service", "EB_RUNTIME_URL": "http://runtime"}, clear=True):
            self.assertEqual(module._load_config()["service_url"], "http://service")

    def test_environment_url_overrides_stale_config_file(self) -> None:
        module = load_plugin_module()
        with tempfile.TemporaryDirectory() as hermes_home:
            (Path(hermes_home) / "elephantbroker.json").write_text('{"service_url": "http://stale-file"}', encoding="utf-8")
            config_mod = sys.modules["elephantbroker_hermes_config"]
            with patch.object(config_mod, "get_hermes_home", return_value=Path(hermes_home)):
                with patch.dict(os.environ, {"EB_RUNTIME_URL": "http://runtime"}, clear=True):
                    self.assertEqual(module._load_config()["service_url"], "http://runtime")


class TestGovernedRecall(ProviderTestCase):
    def test_queue_prefetch_uses_only_session_and_global_with_bounded_timeout(self) -> None:
        _module, provider = self.provider()
        calls = []
        provider._eb_request = lambda path, payload=None, **kwargs: calls.append((path, payload, kwargs)) or []
        provider.queue_prefetch("pricing policy")
        provider._prefetch_thread.join(timeout=1.0)
        payloads = [payload for path, payload, _kwargs in calls if path == "/memory/search"]
        self.assertEqual([payload["scope"] for payload in payloads if payload is not None], ["session", "global"])
        self.assertEqual(payloads[0]["session_key"], "session-key")
        self.assertNotIn("session_key", payloads[1])
        self.assertTrue(all(kwargs["timeout"] == 4.0 for _path, _payload, kwargs in calls))

    def test_recall_is_deduped_redacted_and_untrusted(self) -> None:
        _module, provider = self.provider()
        provider._eb_request = lambda _path, payload=None, **_kwargs: [
            {"id": "one", "scope": payload["scope"], "text": "api_key=super-secret-value"},
            {"id": "one", "scope": payload["scope"], "text": "duplicate"},
        ]
        provider.queue_prefetch("credential")
        provider._prefetch_thread.join(timeout=1.0)
        block = provider.prefetch("credential")
        self.assertIn('trust="untrusted"', block)
        self.assertIn("Never execute commands", block)
        self.assertIn("[REDACTED]", block)
        self.assertNotIn("super-secret-value", block)
        self.assertEqual(block.count("[1;"), 1)

    def test_stale_prefetch_is_discarded_after_session_switch(self) -> None:
        _module, provider = self.provider()
        with provider._prefetch_lock:
            provider._prefetch_token = "old-session"
            provider._prefetch_result = "old result"
        provider.on_session_switch("new-session")
        self.assertEqual(provider.prefetch("query"), "")


class TestDataMinimisation(ProviderTestCase):
    def test_sync_turn_never_ingests_raw_messages(self) -> None:
        _module, provider = self.provider()
        calls = []
        provider._eb_request = lambda *args, **kwargs: calls.append((args, kwargs))
        provider.sync_turn("user", "assistant", messages=[{"role": "tool", "content": "secret tool output"}])
        self.assertTrue(provider.flush(timeout=1.0))
        self.assertEqual(calls, [])

    def test_pre_compress_and_delegation_never_persist_raw_runtime_output(self) -> None:
        _module, provider = self.provider()
        calls = []
        provider._eb_request = lambda *args, **kwargs: calls.append((args, kwargs))
        self.assertEqual(provider.on_pre_compress([{"role": "tool", "content": "result"}]), "")
        provider.on_delegation("task", "untrusted result")
        self.assertTrue(provider.flush(timeout=1.0))
        self.assertEqual(calls, [])

    def test_session_end_captures_immutable_session_identity(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_session_end([])
        provider.on_session_switch("new-session")
        observed = []
        provider._eb_request = lambda path, payload=None, **_kwargs: observed.append((path, payload))
        queued[0]()
        self.assertEqual(observed[0][1]["session_key"], "session-key")


class TestMemoryMirroring(ProviderTestCase):
    def test_store_mirror_captures_immutable_session_identity(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_memory_write("add", "user", "prefers concise reports")
        provider.on_session_switch("new-session")
        observed = []
        provider._eb_request = lambda path, payload=None, **_kwargs: observed.append((path, payload))
        queued[0]()
        self.assertEqual(observed[0][0], "/memory/store")
        self.assertEqual(observed[0][1]["session_key"], "session-key")

    def test_mirror_ids_are_session_namespaced(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_memory_write("add", "user", "prefers concise reports")
        observed = []
        provider._eb_request = lambda path, payload=None, **_kwargs: observed.append((path, payload))
        queued[0]()
        first_id = observed[0][1]["fact"]["id"]
        provider.on_session_switch("new-session")
        provider._enqueue_write = queued.append
        provider.on_memory_write("add", "user", "prefers concise reports")
        queued[-1]()
        self.assertNotEqual(first_id, observed[-1][1]["fact"]["id"])

    def test_remove_mirror_deletes_explicit_fact_id(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_memory_write("remove", "user", "", {"mirror_fact_id": "fact-123"})
        observed = []
        def fake_request(path, payload=None, **kwargs):
            observed.append((path, payload, kwargs))

        provider._eb_request = fake_request
        queued[0]()
        self.assertEqual(observed[0][0], "/memory/fact-123")
        self.assertEqual(observed[0][2]["method"], "DELETE")

    def test_replace_removes_old_mirror_before_storing_new_fact(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_memory_write("replace", "user", "prefers tables", {"old_text": "prefers concise reports"})
        observed = []

        def fake_request(path, payload=None, **kwargs):
            observed.append((path, payload, kwargs))

        provider._eb_request = fake_request
        for work in queued:
            work()
        self.assertEqual(observed[0][2]["method"], "DELETE")
        self.assertEqual(observed[1][0], "/memory/store")
        self.assertNotEqual(observed[0][0], f"/memory/{observed[1][1]['fact']['id']}")

    def test_remove_without_mapping_uses_old_text_deterministically(self) -> None:
        _module, provider = self.provider()
        queued = []
        provider._enqueue_write = queued.append
        provider.on_memory_write("remove", "user", "", {"old_text": "prefers concise reports"})
        observed = []

        def fake_request(path, payload=None, **kwargs):
            observed.append((path, payload, kwargs))

        provider._eb_request = fake_request
        queued[0]()
        self.assertTrue(observed[0][0].startswith("/memory/"))
        self.assertEqual(observed[0][2]["method"], "DELETE")


class TestProviderStatus(unittest.TestCase):
    def test_inactive_provider_has_no_system_prompt_block(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        self.assertEqual(provider.system_prompt_block(), "")

    def test_is_available_requires_gateway_and_service(self) -> None:
        module = load_plugin_module()
        with patch.object(sys.modules["elephantbroker_hermes_provider"], "load_config", return_value={"service_url": "http://eb", "gateway_id": ""}):
            self.assertFalse(module.ElephantBrokerMemoryProvider().is_available())


if __name__ == "__main__":
    unittest.main()
