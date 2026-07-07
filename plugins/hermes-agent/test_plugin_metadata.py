from __future__ import annotations

import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parent


def read_plugin_yaml() -> str:
    return (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")


class TestHermesPluginMetadata(unittest.TestCase):
    def test_plugin_yaml_declares_memory_provider_hooks(self) -> None:
        plugin_yaml = read_plugin_yaml()

        self.assertIn("name: elephantbroker", plugin_yaml)
        self.assertIn("version: 1.0.0", plugin_yaml)
        self.assertIn("provider_type: memory", plugin_yaml)
        self.assertIn("hooks:", plugin_yaml)
        self.assertIn("  - system_prompt_block", plugin_yaml)
        self.assertIn("  - prefetch", plugin_yaml)
        self.assertIn("  - queue_prefetch", plugin_yaml)
        self.assertIn("  - sync_turn", plugin_yaml)
        self.assertIn("  - on_session_end", plugin_yaml)
        self.assertIn("  - on_pre_compress", plugin_yaml)
        self.assertIn("  - on_memory_write", plugin_yaml)
        self.assertIn("  - shutdown", plugin_yaml)


if __name__ == "__main__":
    _ = unittest.main()
