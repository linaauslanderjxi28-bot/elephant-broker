from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


def load_plugin_module():
    path = Path(__file__).with_name("_plugin_eb.py")
    spec = importlib.util.spec_from_file_location("claude_plugin_eb", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load _plugin_eb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestActorHeaders(unittest.TestCase):
    def test_default_headers_include_actor_id_when_env_is_set(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"EB_ACTOR_ID": "actor-123"}, clear=True):
            self.assertEqual(module._default_headers()["X-EB-Actor-Id"], "actor-123")

    def test_default_headers_omit_actor_id_when_env_is_blank(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"EB_ACTOR_ID": "  "}, clear=True):
            self.assertNotIn("X-EB-Actor-Id", module._default_headers())

    def test_default_headers_include_auth_token_when_env_is_set(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"EB_AUTH_TOKEN": " token-test "}, clear=True):
            self.assertEqual(module._default_headers()["X-EB-Auth-Token"], "token-test")

    def test_default_headers_omit_auth_token_when_env_is_blank(self) -> None:
        module = load_plugin_module()
        with patch.dict(os.environ, {"EB_AUTH_TOKEN": "  "}, clear=True):
            self.assertNotIn("X-EB-Auth-Token", module._default_headers())


if __name__ == "__main__":
    unittest.main()
