from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PROJECT_ROOT / "plugins"
MARKETPLACE_MANIFEST = PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN_NAMES = ("gepa-omni", "fleet-optimize")
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


def test_both_portable_manifests_match_agent_plugins_schema_contract() -> None:
    for plugin_name in PLUGIN_NAMES:
        manifest = _load_manifest(PLUGINS_ROOT / plugin_name / "plugin.json")

        assert set(manifest) <= PORTABLE_FIELDS
        assert manifest["$schema"] == PORTABLE_SCHEMA
        assert manifest["name"] == plugin_name
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


def test_each_plugin_discovers_only_its_shipped_skill() -> None:
    for plugin_name in PLUGIN_NAMES:
        skills_root = PLUGINS_ROOT / plugin_name / "skills"
        discovered = sorted(
            path.name
            for path in skills_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )
        assert discovered == [f"{plugin_name}-skill"]


def test_codex_adapters_preserve_shared_manifest_metadata() -> None:
    for plugin_name in PLUGIN_NAMES:
        portable = _load_manifest(PLUGINS_ROOT / plugin_name / "plugin.json")
        codex = _load_manifest(PLUGINS_ROOT / plugin_name / ".codex-plugin" / "plugin.json")

        for field in SHARED_FIELDS:
            assert codex[field] == portable[field]


def test_codex_manifests_expose_trust_links() -> None:
    for plugin_name in PLUGIN_NAMES:
        codex = _load_manifest(PLUGINS_ROOT / plugin_name / ".codex-plugin" / "plugin.json")
        interface = codex["interface"]
        assert isinstance(interface, dict)
        assert interface["privacyPolicyURL"] == "https://github.com/Qredence/gepa-omni/blob/main/PRIVACY.md"
        assert interface["termsOfServiceURL"] == "https://github.com/Qredence/gepa-omni/blob/main/LICENSE"


def test_marketplace_resolves_both_plugins_with_installation_policy() -> None:
    marketplace = _load_manifest(MARKETPLACE_MANIFEST)
    entries = marketplace["plugins"]
    assert isinstance(entries, list)
    by_name = {entry["name"]: entry for entry in entries if isinstance(entry, dict)}

    assert set(by_name) == set(PLUGIN_NAMES)
    for plugin_name in PLUGIN_NAMES:
        entry = by_name[plugin_name]
        source = entry["source"]
        assert source == {"source": "local", "path": f"./plugins/{plugin_name}"}
        assert entry["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert entry["category"] == "Developer Tools"


def test_plugin_payloads_have_local_license_and_distinct_manifests() -> None:
    for plugin_name in PLUGIN_NAMES:
        plugin_root = PLUGINS_ROOT / plugin_name
        assert (plugin_root / "LICENSE").is_file()
        assert (plugin_root / "plugin.json").is_file()
        assert (plugin_root / ".codex-plugin" / "plugin.json").is_file()
        assert (plugin_root / "plugin.json").read_bytes() != (
            plugin_root / ".codex-plugin" / "plugin.json"
        ).read_bytes()


def test_two_plugin_runtimes_do_not_import_each_other() -> None:
    fleet_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PLUGINS_ROOT / "fleet-optimize").rglob("*.py")
    )
    gepa_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PLUGINS_ROOT / "gepa-omni").rglob("*.py")
    )
    assert "gepa_omni" not in fleet_text
    assert "fleet_optimize" not in gepa_text
