from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def load_plugin_module():
    path = Path(__file__).with_name("__init__.py")
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("hermes_elephantbroker_plugin", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Hermes plugin")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestActorHeaders(unittest.TestCase):
    def test_default_headers_include_actor_id_when_env_is_set(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        with patch.dict(os.environ, {"EB_ACTOR_ID": "actor-123"}, clear=True):
            self.assertEqual(provider._default_headers()["X-EB-Actor-Id"], "actor-123")

    def test_default_headers_omit_actor_id_when_env_is_blank(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        with patch.dict(os.environ, {"EB_ACTOR_ID": "  "}, clear=True):
            self.assertNotIn("X-EB-Actor-Id", provider._default_headers())

    def test_default_headers_include_auth_token_when_env_is_set(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        with patch.dict(os.environ, {"EB_AUTH_TOKEN": " token-test "}, clear=True):
            self.assertEqual(provider._default_headers()["X-EB-Auth-Token"], "token-test")

    def test_default_headers_omit_auth_token_when_env_is_blank(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        with patch.dict(os.environ, {"EB_AUTH_TOKEN": "  "}, clear=True):
            self.assertNotIn("X-EB-Auth-Token", provider._default_headers())


class TestClientHTTPError(unittest.TestCase):
    def test_request_raises_structured_http_error_with_json_body(self) -> None:
        load_plugin_module()
        client_mod = sys.modules["elephantbroker_hermes_client"]
        err = client_mod.urllib.error.HTTPError(
            url="http://eb.test/memory/store",
            code=409,
            msg="Conflict",
            hdrs={},
            fp=io.BytesIO(b'{"reason":"near_duplicate_detected","existing_fact_id":"fact-old"}'),
        )

        client = client_mod.ElephantBrokerClient("http://eb.test", "gw-test", "")
        with patch.object(client_mod.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(client_mod.ElephantBrokerHTTPError) as ctx:
                client.request("/memory/store", {"fact": {"text": "duplicate"}})

        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "Conflict")
        self.assertEqual(ctx.exception.json_body["existing_fact_id"], "fact-old")


class TestProviderContract(unittest.TestCase):
    def test_register_adds_memory_provider(self) -> None:
        module = load_plugin_module()

        class Context:
            def __init__(self) -> None:
                self.provider = None

            def register_memory_provider(self, provider) -> None:
                self.provider = provider

        ctx = Context()
        module.register(ctx)

        self.assertIsInstance(ctx.provider, module.ElephantBrokerMemoryProvider)

    def test_sync_turn_does_not_wait_for_existing_thread(self) -> None:
        module = load_plugin_module()
        provider = module.ElephantBrokerMemoryProvider()
        provider._session_key = "session"
        provider._session_id = "00000000-0000-4000-8000-000000000000"
        provider._agent_context = "primary"
        provider._active = True

        class ExistingThread:
            def is_alive(self) -> bool:
                return True

            def join(self, timeout=None) -> None:
                raise AssertionError("sync_turn must not block on a previous sync thread")

        started = []

        class NewThread:
            def __init__(self, target, daemon, name) -> None:
                self.target = target
                self.daemon = daemon
                self.name = name

            def start(self) -> None:
                started.append(self.name)

        provider._sync_thread = ExistingThread()
        writer = sys.modules["elephantbroker_hermes_writer"]
        with patch.object(writer.threading, "Thread", NewThread):
            provider.sync_turn("user", "assistant")

        self.assertEqual(started, ["eb-sync-turn"])


if __name__ == "__main__":
    unittest.main()
