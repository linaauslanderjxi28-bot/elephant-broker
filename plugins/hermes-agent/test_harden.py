from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_plugin_module():
    path = Path(__file__).with_name("__init__.py")
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("hermes_elephantbroker_plugin_harden", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Hermes plugin")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestConfigFallbacks(unittest.TestCase):
    def test_load_config_prefers_eb_service_url(self) -> None:
        module = load_plugin_module()
        env = {"EB_SERVICE_URL": "http://service", "EB_RUNTIME_URL": "http://runtime"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(module._load_config()["service_url"], "http://service")

    def test_load_config_falls_back_to_eb_runtime_url(self) -> None:
        module = load_plugin_module()
        env = {"EB_RUNTIME_URL": "http://runtime", "COGNEE_SERVICE_URL": "http://cognee"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(module._load_config()["service_url"], "http://runtime")

    def test_load_config_falls_back_to_cognee_service_url(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"COGNEE_SERVICE_URL": "http://cognee"}, clear=True):
            self.assertEqual(module._load_config()["service_url"], "http://cognee")

    def test_environment_url_overrides_stale_config_file(self) -> None:
        module = load_plugin_module()
        with tempfile.TemporaryDirectory() as hermes_home:
            config_path = Path(hermes_home) / "elephantbroker.json"
            config_path.write_text('{"service_url": "http://stale-file"}', encoding="utf-8")
            env = {"HERMES_HOME": hermes_home, "EB_RUNTIME_URL": "http://runtime"}

            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(module._load_config()["service_url"], "http://runtime")


class TestTurnSync(unittest.TestCase):
    def test_sync_turn_uses_supplied_messages_verbatim(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        calls = []
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "calling tool", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "content": "tool result", "tool_call_id": "t1"},
        ]

        def fake_request(path, payload=None, **_kwargs):
            calls.append((path, payload or {}))

        provider._eb_request = fake_request
        provider.sync_turn("ignored-user", "ignored-assistant", messages=messages)

        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(calls[0][0], "/memory/ingest-turn")
        self.assertEqual(calls[0][1]["messages"], messages)

    def test_sync_turn_builds_user_assistant_pair_without_messages(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        calls = []

        def fake_request(path, payload=None, **_kwargs):
            calls.append((path, payload or {}))

        provider._eb_request = fake_request
        provider.sync_turn("hello", "world")

        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(
            calls[0][1]["messages"],
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
        )

    def test_sync_turn_skips_non_primary_context(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._agent_context = "worker"
        calls = []
        provider._eb_request = lambda *args, **kwargs: calls.append((args, kwargs))

        provider.sync_turn("hello", "world")

        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(calls, [])


class TestBackgroundWrites(unittest.TestCase):
    def test_flush_drains_writes_in_fifo_order(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        observed = []

        def fake_request(_path, payload=None, **_kwargs):
            observed.append((payload or {})["messages"][0]["content"])

        provider._eb_request = fake_request
        provider.sync_turn("one", "assistant")
        provider.sync_turn("two", "assistant")
        provider.sync_turn("three", "assistant")

        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(observed, ["one", "two", "three"])

    def test_on_pre_compress_enqueues_complete_messages(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        calls = []
        messages = [{"role": "user", "content": "compress me"}]

        def fake_request(path, payload=None, **_kwargs):
            calls.append((path, payload or {}))

        provider._eb_request = fake_request

        self.assertEqual(provider.on_pre_compress(messages), "")
        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(calls[0][0], "/memory/ingest-turn")
        self.assertEqual(calls[0][1]["source"], "pre_compress")
        self.assertEqual(calls[0][1]["messages"], messages)

    def test_session_end_is_ordered_after_pending_writes(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"
        provider._profile_name = "coding"
        calls = []

        def fake_request(path, payload=None, **_kwargs):
            calls.append((path, payload or {}))

        provider._eb_request = fake_request
        provider.sync_turn("before end", "assistant")
        provider.on_session_end([])

        self.assertEqual([path for path, _payload in calls], ["/memory/ingest-turn", "/sessions/end"])

    def test_background_write_failures_are_logged(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._active = True
        provider._session_key = "session-key"
        provider._session_id = "00000000-0000-4000-8000-000000000001"

        def failing_request(*_args, **_kwargs):
            raise RuntimeError("boom")

        provider._eb_request = failing_request
        writer = sys.modules["elephantbroker_hermes_writer"]
        with self.assertLogs(writer.logger, level="WARNING") as logs:
            provider.on_memory_write("add", "general", "remember this")
            self.assertTrue(provider.flush(timeout=2.0))

        self.assertTrue(any("background write failed" in line for line in logs.output))

    def test_sync_turn_skips_when_gateway_is_inactive(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        calls = []
        provider._eb_request = lambda *args, **kwargs: calls.append((args, kwargs))

        provider.sync_turn("hello", "world")

        self.assertTrue(provider.flush(timeout=2.0))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
