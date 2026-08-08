from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTABLE_MANIFEST = PROJECT_ROOT / "plugin.json"
CODEX_MANIFEST = PROJECT_ROOT / ".codex-plugin" / "plugin.json"
PORTABLE_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PORTABLE_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SHARED_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)


def _load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_portable_manifest_matches_agent_plugins_1_schema_contract() -> None:
    manifest = _load_manifest(PORTABLE_MANIFEST)

    assert set(manifest) <= PORTABLE_FIELDS
    assert manifest["$schema"] == PORTABLE_SCHEMA
    name = manifest["name"]
    assert isinstance(name, str)
    assert 1 <= len(name) <= 64
    assert PLUGIN_NAME_PATTERN.fullmatch(name)

    author = manifest["author"]
    assert isinstance(author, dict)
    assert set(author) <= {"name", "email", "url"}
    assert all(isinstance(value, str) for value in author.values())

    assert isinstance(manifest["version"], str)
    assert isinstance(manifest["description"], str)
    assert isinstance(manifest["homepage"], str)
    assert isinstance(manifest["repository"], str)
    assert isinstance(manifest["license"], str)
    keywords = manifest["keywords"]
    assert isinstance(keywords, list)
    assert all(isinstance(keyword, str) for keyword in keywords)


def test_portable_manifest_discovers_the_shipped_skill() -> None:
    skills_root = PROJECT_ROOT / "skills"
    discovered = sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )

    assert discovered == ["gepa-omni-skill"]


def test_codex_adapter_preserves_shared_manifest_metadata() -> None:
    portable = _load_manifest(PORTABLE_MANIFEST)
    codex = _load_manifest(CODEX_MANIFEST)

    for field in SHARED_FIELDS:
        assert codex[field] == portable[field]
