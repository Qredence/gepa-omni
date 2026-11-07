#!/usr/bin/env python3
"""Stage one runtime-only marketplace plugin payload outside the checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
PUBLIC_PACKAGE_ROOT = REPO_ROOT / "packages" / "gepa-omni" / "src" / "gepa_omni"
PUBLIC_PACKAGE_REPO_PREFIX = Path("packages/gepa-omni/src/gepa_omni")
PUBLIC_PACKAGE_DESTINATION = Path("skills/gepa-omni-skill/scripts/gepa_omni")
_FORBIDDEN_INDEX_MODES = frozenset({"120000", "160000"})
PLUGIN_NAMES: Final = ("fleet-optimize",)
DEFAULT_PLUGIN_NAME = "fleet-optimize"
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


def _index_entries(repo_prefix: Path) -> list[tuple[str, Path]]:
    command = [
        "git",
        "-C",
        str(REPO_ROOT),
        "ls-files",
        "-z",
        "-s",
        "--",
        str(repo_prefix),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"could not enumerate tracked files under {repo_prefix}: {detail}")

    intent = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "-z", "--diff-filter=A", "--", str(repo_prefix)],
        capture_output=True,
        check=True,
    )
    if intent.stdout:
        raise ValueError("intent-to-add entries cannot be staged as a plugin payload")
    entries: list[tuple[str, Path]] = []
    for entry in result.stdout.decode().split("\0"):
        if not entry:
            continue
        mode, _, _, repo_path = entry.split(maxsplit=3)
        if mode in _FORBIDDEN_INDEX_MODES:
            raise ValueError(f"staging does not support symlink or gitlink index entries: {repo_path}")
        entries.append((mode, Path(repo_path)))
    return entries


def _tracked_paths(plugin_name: str, package_format: str) -> list[Path]:
    _plugin_root(plugin_name)
    try:
        shipped_paths = SHIPPED_PATHS_BY_FORMAT[package_format]
    except KeyError as exc:
        choices = ", ".join(sorted(SHIPPED_PATHS_BY_FORMAT))
        raise ValueError(f"unsupported package format {package_format!r}; choose one of {choices}") from exc

    plugin_prefix = Path("plugins") / plugin_name
    tracked_paths: list[Path] = []
    for _mode, repo_path in _index_entries(plugin_prefix):
        relative_path = repo_path.relative_to(plugin_prefix)
        if any(relative_path == root or root in relative_path.parents for root in shipped_paths):
            tracked_paths.append(relative_path)
    required = Path("plugin.json") if package_format == "portable" else Path(".codex-plugin/plugin.json")
    if required not in tracked_paths:
        raise ValueError(f"required manifest is absent from the git index: {plugin_prefix / required}")
    return sorted(set(tracked_paths))


def _tracked_public_package_py_paths() -> list[Path]:
    repo_paths = [
        repo_path for _mode, repo_path in _index_entries(PUBLIC_PACKAGE_REPO_PREFIX) if repo_path.suffix == ".py"
    ]
    if PUBLIC_PACKAGE_REPO_PREFIX / "__init__.py" not in repo_paths:
        raise ValueError("public GEPA Omni package has no indexed __init__.py")
    return sorted(repo_paths)


def _write_blob_from_index(repo_path: Path, target: Path) -> None:
    show = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f":{repo_path.as_posix()}"],
        capture_output=True,
        check=False,
    )
    if show.returncode != 0:
        detail = show.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"could not read indexed file {repo_path}: {detail}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(show.stdout)


def _stage_public_package(destination: Path) -> None:
    if not PUBLIC_PACKAGE_ROOT.is_dir():
        raise FileNotFoundError(f"public GEPA Omni package does not exist: {PUBLIC_PACKAGE_ROOT}")
    for repo_path in _tracked_public_package_py_paths():
        relative = repo_path.relative_to(PUBLIC_PACKAGE_REPO_PREFIX)
        target = destination / PUBLIC_PACKAGE_DESTINATION / relative
        _write_blob_from_index(repo_path, target)


def stage(
    output: Path,
    plugin_name: str = DEFAULT_PLUGIN_NAME,
    package_format: str = DEFAULT_PACKAGE_FORMAT,
) -> Path:
    """Copy one allowlisted plugin format into *output*."""

    plugin_root = _plugin_root(plugin_name)
    destination = _validate_output(output, plugin_name=plugin_name)
    tracked_paths = _tracked_paths(plugin_name, package_format)
    _tracked_public_package_py_paths()
    destination.mkdir(parents=True, exist_ok=True)

    plugin_prefix = plugin_root.relative_to(REPO_ROOT)
    for relative_path in tracked_paths:
        _write_blob_from_index(plugin_prefix / relative_path, destination / relative_path)
    _stage_public_package(destination)
    return destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin",
        choices=PLUGIN_NAMES,
        default=DEFAULT_PLUGIN_NAME,
        help="Plugin payload to stage (default: fleet-optimize)",
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
