from __future__ import annotations

import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]
REPO_ROOT = PLUGIN_ROOT.parent


class TestAntigravityManifest(unittest.TestCase):
    def test_plugin_json_uses_native_name_shape(self) -> None:
        manifest = (PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8")
        name_match = re.search(r'"name"\s*:\s*"([a-zA-Z0-9-_]+)"', manifest)
        version_match = re.search(r'"version"\s*:\s*"(\d+\.\d+\.\d+)"', manifest)

        self.assertIsNotNone(name_match)
        self.assertEqual(name_match.group(1) if name_match else "", "elephantbroker-memory")
        self.assertIsNotNone(version_match)
        self.assertIn('"description"', manifest)

    def test_readme_documents_native_install_location(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("~/.gemini/antigravity-cli/plugins/elephantbroker-memory/", readme)

    def test_hook_timeouts_are_milliseconds(self) -> None:
        hooks = (PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8")
        timeouts: list[int] = []
        for match in re.finditer(r'"timeout"\s*:\s*(\d+)', hooks):
            timeouts.append(int(match.group(1)))

        self.assertEqual(timeouts, [15000, 20000, 30000])
        self.assertTrue(all(timeout >= 1000 for timeout in timeouts))


if __name__ == "__main__":
    _ = unittest.main()
