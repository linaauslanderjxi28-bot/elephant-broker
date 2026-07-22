from __future__ import annotations

import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]
PROJECT_ROOT = Path(__file__).parents[3]  # plugins/claude-code/scripts/ -> repo root


class TestClaudePluginManifest(unittest.TestCase):
    def test_marketplace_source_resolves_to_actual_plugin_directory(self) -> None:
        """Marketplace entry source must resolve to an existing plugin directory.

        The marketplace.json at plugins/.claude-plugin/marketplace.json has a
        ``source`` field per plugin entry.  The field is relative to
        ``plugins/``.  This test asserts that the resolved path exists and
        identifies the same plugin as the test's own PLUGIN_ROOT.
        """
        marketplace = json.loads(
            (
                PROJECT_ROOT / "plugins" / ".claude-plugin" / "marketplace.json"
            ).read_text(encoding="utf-8"),
        )

        plugins_dir = PROJECT_ROOT / "plugins"
        plugin_entry = next(
            p for p in marketplace["plugins"] if p["name"] == "elephantbroker-memory"
        )
        resolved_source = (plugins_dir / plugin_entry["source"]).resolve()

        self.assertTrue(
            resolved_source.exists(),
            msg=(
                f"Marketplace source '{plugin_entry['source']}' resolves to "
                f"{resolved_source}, which does not exist. "
                f"Expected it to resolve to the actual plugin at {PLUGIN_ROOT.resolve()}"
            ),
        )

        self.assertEqual(
            resolved_source,
            PLUGIN_ROOT.resolve(),
            msg=(
                f"Marketplace source '{plugin_entry['source']}' resolves to "
                f"{resolved_source}, but the actual plugin lives at "
                f"{PLUGIN_ROOT.resolve()}"
            ),
        )

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
