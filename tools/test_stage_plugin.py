from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stage_plugin import CANONICAL_PLUGIN_NAME, REPO_ROOT, stage


class StagePluginTests(unittest.TestCase):
    def test_stage_contains_only_runtime_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / CANONICAL_PLUGIN_NAME
            staged = stage(output)

            manifest = json.loads(
                (staged / ".codex-plugin" / "plugin.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["name"], CANONICAL_PLUGIN_NAME)
            self.assertTrue((staged / "skills").is_dir())
            self.assertTrue((staged / "LICENSE").is_file())
            for development_only in (
                "tests",
                "tools",
                "pyproject.toml",
                ".DS_Store",
            ):
                self.assertFalse((staged / development_only).exists())

            self.assertFalse(
                any(path.name == ".DS_Store" for path in staged.rglob("*"))
            )

    def test_stage_requires_canonical_external_output(self) -> None:
        with self.assertRaises(ValueError):
            stage(REPO_ROOT / CANONICAL_PLUGIN_NAME)

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                stage(Path(temp_dir) / "wrong-name")

    def test_stage_rejects_non_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / CANONICAL_PLUGIN_NAME
            output.mkdir()
            (output / "existing.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                stage(output)


if __name__ == "__main__":
    unittest.main()
