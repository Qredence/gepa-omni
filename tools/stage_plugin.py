#!/usr/bin/env python3
"""Stage one runtime-only marketplace plugin payload outside the checkout."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
PUBLIC_PACKAGE_ROOT = REPO_ROOT / "packages" / "gepa-omni" / "src" / "gepa_omni"
PUBLIC_PACKAGE_DESTINATION = Path("skills/gepa-omni-skill/scripts/gepa_omni")
PLUGIN_NAMES: Final = ("gepa-omni", "fleet-optimize")
DEFAULT_PLUGIN_NAME = "gepa-omni"
# Kept as a compatibility alias for callers of the original single-plugin tool.
CANONICAL_PLUGIN_NAME = DEFAULT_PLUGIN_NAME
DEFAULT_PACKAGE_FORMAT: Final = "portable"
SHIPPED_PATHS_BY_FORMAT: Final = {
    "portable": (Path("plugin.json"), Path("skills"), Path("LICENSE")),
    "codex": (Path(".codex-plugin"), Path("skills"), Path("LICENSE")),
}


def _plugin_root(plugin_name: str) -> Path:
    if plugin_name not in PLUGIN_NAMES:
        choices = ", ".join(PLUGIN_NAMES)
        raise ValueError(f"unknown plugin {plugin_name!r}; choose one of {choices}")
    plugin_root = PLUGINS_ROOT / plugin_name
    if not plugin_root.is_dir():
        raise FileNotFoundError(f"plugin payload does not exist: {plugin_root}")
    return plugin_root


def _validate_output(output: Path, *, plugin_name: str) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("--output must be outside the development checkout")
    if resolved.name != plugin_name:
        raise ValueError(f"--output directory must be named {plugin_name!r}")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("--output already exists and is not a directory")
        if any(resolved.iterdir()):
            raise ValueError("--output must be missing or an empty directory")
    return resolved


def _tracked_paths(plugin_name: str, package_format: str) -> list[Path]:
    _plugin_root(plugin_name)
    try:
        shipped_paths = SHIPPED_PATHS_BY_FORMAT[package_format]
    except KeyError as exc:
        choices = ", ".join(sorted(SHIPPED_PATHS_BY_FORMAT))
        raise ValueError(f"unsupported package format {package_format!r}; choose one of {choices}") from exc

    command = [
        "git",
        "-C",
        str(REPO_ROOT),
        "ls-files",
        "-z",
        "--",
        *(str(Path("plugins") / plugin_name / path) for path in shipped_paths),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"could not enumerate tracked plugin files: {detail}")

    plugin_prefix = Path("plugins") / plugin_name
    tracked_paths = [Path(path).relative_to(plugin_prefix) for path in result.stdout.decode().split("\0") if path]

    # Manifests and the license are package metadata. Allow them to be staged
    # from a dirty worktree before the implementation is added to the index;
    # runtime files remain strictly Git-tracked so accidental files cannot ship.
    metadata = Path("plugin.json") if package_format == "portable" else Path(".codex-plugin") / "plugin.json"
    metadata_paths = (metadata, Path("LICENSE"))
    for metadata_path in metadata_paths:
        if metadata_path not in tracked_paths and (PLUGINS_ROOT / plugin_name / metadata_path).is_file():
            tracked_paths.insert(0, metadata_path)
    return sorted(set(tracked_paths))


def stage(
    output: Path,
    plugin_name: str = DEFAULT_PLUGIN_NAME,
    package_format: str = DEFAULT_PACKAGE_FORMAT,
) -> Path:
    """Copy one allowlisted plugin format into *output*."""

    plugin_root = _plugin_root(plugin_name)
    destination = _validate_output(output, plugin_name=plugin_name)
    destination.mkdir(parents=True, exist_ok=True)

    tracked_paths = _tracked_paths(plugin_name, package_format)
    for relative_path in tracked_paths:
        source = plugin_root / relative_path
        target = destination / relative_path
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"staging does not support symlink or non-file: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if plugin_name == "gepa-omni":
        if not PUBLIC_PACKAGE_ROOT.is_dir():
            raise FileNotFoundError(f"public GEPA Omni package does not exist: {PUBLIC_PACKAGE_ROOT}")
        # The skill's preflight and runtime scripts are executable from a
        # staged payload without the repository's uv source mapping. Bundle
        # the same public package beside those scripts so ``import
        # gepa_omni`` has no checkout-only dependency.
        for source in sorted(PUBLIC_PACKAGE_ROOT.rglob("*.py")):
            relative_path = PUBLIC_PACKAGE_DESTINATION / source.relative_to(PUBLIC_PACKAGE_ROOT)
            target = destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin",
        choices=PLUGIN_NAMES,
        default=DEFAULT_PLUGIN_NAME,
        help="Plugin payload to stage (default: gepa-omni)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty external directory named after the selected plugin",
    )
    parser.add_argument(
        "--format",
        dest="package_format",
        choices=tuple(SHIPPED_PATHS_BY_FORMAT),
        default=DEFAULT_PACKAGE_FORMAT,
        help="Package format to stage (default: portable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        destination = stage(args.output, plugin_name=args.plugin, package_format=args.package_format)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"plugin staging error: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
