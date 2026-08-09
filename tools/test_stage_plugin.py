from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from stage_plugin import CANONICAL_PLUGIN_NAME, REPO_ROOT, stage


class StagePluginTests(unittest.TestCase):
    @staticmethod
    def _is_tracked(relative_path: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", relative_path],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def test_portable_stage_contains_only_runtime_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / CANONICAL_PLUGIN_NAME
            staged = stage(output)

            manifest = json.loads((staged / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], CANONICAL_PLUGIN_NAME)
            self.assertTrue((staged / "plugin.json").is_file())
            self.assertTrue((staged / "skills").is_dir())
            self.assertTrue((staged / "LICENSE").is_file())
            for relative_path in (
                "skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md",
                "skills/gepa-omni-skill/scripts/native_omni/core.py",
            ):
                self.assertEqual(
                    (staged / relative_path).is_file(),
                    self._is_tracked(relative_path),
                    relative_path,
                )
            self.assertFalse((staged / ".codex-plugin").exists())
            for development_only in (
                "tests",
                "tools",
                "pyproject.toml",
                ".DS_Store",
            ):
                self.assertFalse((staged / development_only).exists())

            self.assertFalse(any(path.name == ".DS_Store" for path in staged.rglob("*")))

    def test_codex_stage_contains_legacy_runtime_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / CANONICAL_PLUGIN_NAME
            staged = stage(output, package_format="codex")

            manifest = json.loads((staged / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], CANONICAL_PLUGIN_NAME)
            self.assertTrue((staged / ".codex-plugin").is_dir())
            self.assertTrue((staged / "skills").is_dir())
            self.assertTrue((staged / "LICENSE").is_file())
            for relative_path in (
                "skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md",
                "skills/gepa-omni-skill/scripts/native_omni/runners.py",
            ):
                self.assertEqual(
                    (staged / relative_path).is_file(),
                    self._is_tracked(relative_path),
                    relative_path,
                )
            self.assertFalse((staged / "plugin.json").exists())

    def test_stage_rejects_unknown_package_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "unsupported package format"):
                stage(Path(temp_dir) / CANONICAL_PLUGIN_NAME, package_format="unknown")

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

    def test_stage_omits_untracked_runtime_files(self) -> None:
        marker = REPO_ROOT / "skills" / "gepa-omni-skill" / "scripts" / "native_omni" / "untracked-runtime-marker.py"
        marker.write_text("must not ship", encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                staged = stage(Path(temp_dir) / CANONICAL_PLUGIN_NAME)
                self.assertFalse(
                    (staged / "skills" / "gepa-omni-skill" / "scripts" / "native_omni" / marker.name).exists()
                )
        finally:
            marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
