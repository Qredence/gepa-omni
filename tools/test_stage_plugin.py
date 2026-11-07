from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from stage_plugin import DEFAULT_PLUGIN_NAME, REPO_ROOT, stage


class StagePluginTests(unittest.TestCase):
    def test_portable_stage_contains_only_runtime_payload(self) -> None:
        plugin_name = DEFAULT_PLUGIN_NAME
        with tempfile.TemporaryDirectory() as temp_dir:
            staged = stage(Path(temp_dir) / plugin_name, plugin_name=plugin_name)

            manifest = json.loads((staged / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], plugin_name)
            index_manifest = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", f":plugins/{plugin_name}/plugin.json"],
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual((staged / "plugin.json").read_bytes(), index_manifest)
            self.assertTrue((staged / "LICENSE").is_file())
            package_root = staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni"
            self.assertTrue((package_root / "__init__.py").is_file())
            self.assertTrue((package_root / "api.py").is_file())
            self.assertTrue((staged / "skills" / "gepa-omni-skill" / "THIRD_PARTY_NOTICES.md").is_file())
            self.assertTrue((staged / "skills" / "fleet-optimize-skill" / "SKILL.md").is_file())
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

    def test_codex_stage_contains_runtime_payload(self) -> None:
        plugin_name = DEFAULT_PLUGIN_NAME
        with tempfile.TemporaryDirectory() as temp_dir:
            staged = stage(Path(temp_dir) / plugin_name, plugin_name=plugin_name, package_format="codex")

            manifest = json.loads((staged / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], plugin_name)
            index_manifest = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", f":plugins/{plugin_name}/.codex-plugin/plugin.json"],
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual((staged / ".codex-plugin" / "plugin.json").read_bytes(), index_manifest)
            self.assertTrue((staged / "LICENSE").is_file())
            package_root = staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni"
            self.assertTrue((package_root / "__init__.py").is_file())
            self.assertTrue((package_root / "security.py").is_file())
            self.assertTrue((staged / "skills" / "gepa-omni-skill" / "THIRD_PARTY_NOTICES.md").is_file())
            self.assertTrue((staged / "skills" / "fleet-optimize-skill" / "SKILL.md").is_file())
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

    def test_stage_uses_index_and_omits_untracked_files(self) -> None:
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
        tracked_skill = REPO_ROOT / "plugins" / DEFAULT_PLUGIN_NAME / "skills" / "gepa-omni-skill" / "SKILL.md"
        skill_repo_path = f"plugins/{DEFAULT_PLUGIN_NAME}/skills/gepa-omni-skill/SKILL.md"
        untracked_package = REPO_ROOT / "packages" / "gepa-omni" / "src" / "gepa_omni" / "untracked_package_marker.py"
        tracked_package = REPO_ROOT / "packages" / "gepa-omni" / "src" / "gepa_omni" / "__init__.py"
        package_repo_path = "packages/gepa-omni/src/gepa_omni/__init__.py"
        dirty_suffix = "\n# dirty-working-tree-marker\n"
        original_init = tracked_package.read_text(encoding="utf-8")
        original_skill = tracked_skill.read_text(encoding="utf-8")
        try:
            tracked_package.write_text(original_init + dirty_suffix, encoding="utf-8")
            tracked_skill.write_text(original_skill + dirty_suffix, encoding="utf-8")
            marker.write_text("must not ship", encoding="utf-8")
            untracked_package.write_text("must not ship", encoding="utf-8")
            index_init = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", f":{package_repo_path}"],
                capture_output=True,
                check=True,
            ).stdout.decode()
            index_skill = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", f":{skill_repo_path}"],
                capture_output=True,
                check=True,
            ).stdout.decode()
            with tempfile.TemporaryDirectory() as temp_dir:
                staged = stage(Path(temp_dir) / DEFAULT_PLUGIN_NAME)
                self.assertFalse(
                    (staged / "skills" / "gepa-omni-skill" / "scripts" / "native_omni" / marker.name).exists()
                )
                staged_init = staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni" / "__init__.py"
                staged_skill = staged / "skills" / "gepa-omni-skill" / "SKILL.md"
                self.assertFalse(
                    (staged / "skills" / "gepa-omni-skill" / "scripts" / "gepa_omni" / untracked_package.name).exists()
                )
                self.assertEqual(staged_init.read_text(encoding="utf-8"), index_init)
                self.assertEqual(staged_skill.read_text(encoding="utf-8"), index_skill)
                self.assertNotIn("dirty-working-tree-marker", staged_init.read_text(encoding="utf-8"))
                self.assertNotIn("dirty-working-tree-marker", staged_skill.read_text(encoding="utf-8"))
        finally:
            marker.unlink(missing_ok=True)
            untracked_package.unlink(missing_ok=True)
            tracked_package.write_text(original_init, encoding="utf-8")
            tracked_skill.write_text(original_skill, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()


def test_index_requirements_and_intent_to_add_in_isolated_repository(tmp_path, monkeypatch):
    import stage_plugin as staging
    import pytest

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init")
    monkeypatch.setattr(staging, "REPO_ROOT", repo)
    monkeypatch.setattr(staging, "PLUGINS_ROOT", repo / "plugins")
    package = repo / staging.PUBLIC_PACKAGE_REPO_PREFIX
    monkeypatch.setattr(staging, "PUBLIC_PACKAGE_ROOT", package)
    plugin = repo / "plugins/fleet-optimize"
    plugin.mkdir(parents=True)
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (plugin / "LICENSE").write_text("MIT")
    git("add", ".")
    for fmt in ("portable", "codex"):
        with pytest.raises(ValueError, match="required manifest"):
            staging.stage(tmp_path / fmt / "fleet-optimize", package_format=fmt)
    (plugin / "plugin.json").write_text("{}")
    git("add", ".")
    (package / "__init__.py").write_text("dirty content")
    staged = staging.stage(tmp_path / "empty" / "fleet-optimize")
    assert (staged / staging.PUBLIC_PACKAGE_DESTINATION / "__init__.py").read_text() == ""
    intent = package / "intent.py"
    intent.write_text("not staged")
    git("add", "-N", str(intent))
    with pytest.raises(ValueError, match="intent-to-add"):
        staging.stage(tmp_path / "intent" / "fleet-optimize")
    assert not (tmp_path / "intent" / "fleet-optimize").exists()
    git("reset", "--", str(intent))
    git("rm", "--cached", "-f", str(package / "__init__.py"))
    with pytest.raises(ValueError, match="__init__"):
        staging.stage(tmp_path / "missing-package" / "fleet-optimize")
