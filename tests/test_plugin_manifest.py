from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PROJECT_ROOT / "plugins"
MARKETPLACE_MANIFEST = PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN_NAME = "fleet-optimize"
SKILL_NAMES = ("fleet-optimize-skill", "gepa-omni-skill")
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


def test_portable_manifest_matches_agent_plugins_schema_contract() -> None:
    manifest = _load_manifest(PLUGINS_ROOT / PLUGIN_NAME / "plugin.json")

    assert set(manifest) <= PORTABLE_FIELDS
    assert manifest["$schema"] == PORTABLE_SCHEMA
    assert manifest["name"] == PLUGIN_NAME
    name = manifest["name"]
    assert isinstance(name, str)
    assert 1 <= len(name) <= 64
    assert PLUGIN_NAME_PATTERN.fullmatch(name)

    author = manifest["author"]
    assert isinstance(author, dict)
    assert set(author) <= {"name", "email", "url"}
    assert all(isinstance(value, str) for value in author.values())

    assert manifest["version"] == "0.2.0"
    assert isinstance(manifest["description"], str)
    assert isinstance(manifest["homepage"], str)
    assert isinstance(manifest["repository"], str)
    assert isinstance(manifest["license"], str)
    keywords = manifest["keywords"]
    assert isinstance(keywords, list)
    assert all(isinstance(keyword, str) for keyword in keywords)


def test_plugin_discovers_both_shipped_skills() -> None:
    skills_root = PLUGINS_ROOT / PLUGIN_NAME / "skills"
    discovered = sorted(
        path.name for path in skills_root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
    )
    assert discovered == sorted(SKILL_NAMES)


def test_codex_adapter_preserves_shared_manifest_metadata() -> None:
    portable = _load_manifest(PLUGINS_ROOT / PLUGIN_NAME / "plugin.json")
    codex = _load_manifest(PLUGINS_ROOT / PLUGIN_NAME / ".codex-plugin" / "plugin.json")

    for field in SHARED_FIELDS:
        assert codex[field] == portable[field]


def test_codex_manifest_exposes_trust_links() -> None:
    codex = _load_manifest(PLUGINS_ROOT / PLUGIN_NAME / ".codex-plugin" / "plugin.json")
    interface = codex["interface"]
    assert isinstance(interface, dict)
    assert interface["displayName"] == "Fleet Optimize"
    assert interface["privacyPolicyURL"] == "https://github.com/Qredence/gepa-omni/blob/main/PRIVACY.md"
    assert interface["termsOfServiceURL"] == "https://github.com/Qredence/gepa-omni/blob/main/LICENSE"
    prompts = interface["defaultPrompt"]
    assert isinstance(prompts, list)
    assert any("Omni" in prompt for prompt in prompts)
    assert any("parallel" in prompt for prompt in prompts)


def test_marketplace_resolves_fleet_optimize_with_installation_policy() -> None:
    marketplace = _load_manifest(MARKETPLACE_MANIFEST)
    entries = marketplace["plugins"]
    assert isinstance(entries, list)
    by_name = {entry["name"]: entry for entry in entries if isinstance(entry, dict)}

    assert set(by_name) == {PLUGIN_NAME}
    entry = by_name[PLUGIN_NAME]
    source = entry["source"]
    assert source == {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Developer Tools"


def test_plugin_payload_has_local_license_and_distinct_manifests() -> None:
    plugin_root = PLUGINS_ROOT / PLUGIN_NAME
    assert (plugin_root / "LICENSE").is_file()
    assert (plugin_root / "plugin.json").is_file()
    assert (plugin_root / ".codex-plugin" / "plugin.json").is_file()
    assert (plugin_root / "plugin.json").read_bytes() != (plugin_root / ".codex-plugin" / "plugin.json").read_bytes()


def test_skill_runtimes_do_not_import_each_other() -> None:
    skills_root = PLUGINS_ROOT / PLUGIN_NAME / "skills"
    fleet_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (skills_root / "fleet-optimize-skill").rglob("*.py")
    )
    gepa_text = "\n".join(path.read_text(encoding="utf-8") for path in (skills_root / "gepa-omni-skill").rglob("*.py"))
    assert "gepa_omni" not in fleet_text
    assert "fleet_optimize" not in gepa_text
