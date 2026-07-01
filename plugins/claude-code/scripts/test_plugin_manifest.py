from __future__ import annotations

import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]


class TestClaudePluginManifest(unittest.TestCase):
    def test_claude_plugin_metadata_is_under_claude_plugin_directory(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"),
        )

        self.assertEqual(manifest["name"], "elephantbroker-memory")
        self.assertIn("claude-code", manifest["keywords"])

    def test_hooks_file_uses_claude_plugin_wrapper_shape(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(set(hooks), {"hooks"})
        self.assertIn("SessionStart", hooks["hooks"])
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("PostToolUse", hooks["hooks"])
        self.assertIn("SessionEnd", hooks["hooks"])


if __name__ == "__main__":
    unittest.main()
