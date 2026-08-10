from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from stage_plugin import DEFAULT_PLUGIN_NAME, PLUGIN_NAMES, REPO_ROOT, stage


class StagePluginTests(unittest.TestCase):
    @staticmethod
    def _is_tracked(relative_path: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", relative_path],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def test_portable_stage_contains_only_runtime_payload_for_each_plugin(self) -> None:
        for plugin_name in PLUGIN_NAMES:
            with self.subTest(plugin_name=plugin_name), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / plugin_name
                staged = stage(output, plugin_name=plugin_name)

                manifest = json.loads((staged / "plugin.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["name"], plugin_name)
                self.assertTrue((staged / "plugin.json").is_file())
                self.assertTrue((staged / "LICENSE").is_file())
                if plugin_name == "gepa-omni":
                    package_root = staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni"
                    self.assertTrue((package_root / "__init__.py").is_file())
                    self.assertTrue((package_root / "api.py").is_file())
                expected_skill = (
                    "skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md"
                    if plugin_name == "gepa-omni"
                    else "skills/fleet-optimize-skill/SKILL.md"
                )
                self.assertEqual(
                    (staged / expected_skill).is_file(),
                    self._is_tracked(f"plugins/{plugin_name}/{expected_skill}"),
                    expected_skill,
                )
                self.assertFalse((staged / ".codex-plugin").exists())
                for development_only in (
                    "tests",
                    "tools",
                    "pyproject.toml",
                    "uv.lock",
                    ".DS_Store",
                ):
                    self.assertFalse((staged / development_only).exists())

                self.assertFalse(any(path.name == ".DS_Store" for path in staged.rglob("*")))

    def test_codex_stage_contains_runtime_payload_for_each_plugin(self) -> None:
        for plugin_name in PLUGIN_NAMES:
            with self.subTest(plugin_name=plugin_name), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / plugin_name
                staged = stage(output, plugin_name=plugin_name, package_format="codex")

                manifest = json.loads((staged / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["name"], plugin_name)
                self.assertTrue((staged / ".codex-plugin").is_dir())
                self.assertTrue((staged / "LICENSE").is_file())
                if plugin_name == "gepa-omni":
                    package_root = staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni"
                    self.assertTrue((package_root / "__init__.py").is_file())
                    self.assertTrue((package_root / "security.py").is_file())
                expected_skill = (
                    "skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md"
                    if plugin_name == "gepa-omni"
                    else "skills/fleet-optimize-skill/SKILL.md"
                )
                self.assertEqual(
                    (staged / expected_skill).is_file(),
                    self._is_tracked(f"plugins/{plugin_name}/{expected_skill}"),
                    expected_skill,
                )
                self.assertFalse((staged / "plugin.json").exists())

    def test_stage_rejects_unknown_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "unknown plugin"):
                stage(Path(temp_dir) / "unknown", plugin_name="unknown")

    def test_stage_rejects_unknown_package_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "unsupported package format"):
                stage(Path(temp_dir) / DEFAULT_PLUGIN_NAME, package_format="unknown")

    def test_stage_requires_canonical_external_output(self) -> None:
        with self.assertRaises(ValueError):
            stage(REPO_ROOT / DEFAULT_PLUGIN_NAME)

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                stage(Path(temp_dir) / "wrong-name")

    def test_stage_rejects_non_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / DEFAULT_PLUGIN_NAME
            output.mkdir()
            (output / "existing.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                stage(output)

    def test_stage_omits_untracked_runtime_files(self) -> None:
        marker = (
            REPO_ROOT
            / "plugins"
            / DEFAULT_PLUGIN_NAME
            / "skills"
            / "gepa-omni-skill"
            / "scripts"
            / "native_omni"
            / "untracked-runtime-marker.py"
        )
        marker.write_text("must not ship", encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                staged = stage(Path(temp_dir) / DEFAULT_PLUGIN_NAME)
                self.assertFalse(
                    (staged / "skills" / "gepa-omni-skill" / "scripts" / "native_omni" / marker.name).exists()
                )
        finally:
            marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
