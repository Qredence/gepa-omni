#!/usr/bin/env python3
"""Stage the runtime-only GEPA plugin payload outside the checkout."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PLUGIN_NAME = "gepa-omni"
DEFAULT_PACKAGE_FORMAT: Final = "portable"
SHIPPED_PATHS_BY_FORMAT: Final = {
    "portable": (Path("plugin.json"), Path("skills"), Path("LICENSE")),
    "codex": (Path(".codex-plugin"), Path("skills"), Path("LICENSE")),
}


def _validate_output(output: Path) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("--output must be outside the development checkout")
    if resolved.name != CANONICAL_PLUGIN_NAME:
        raise ValueError(f"--output directory must be named {CANONICAL_PLUGIN_NAME!r}")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("--output already exists and is not a directory")
        if any(resolved.iterdir()):
            raise ValueError("--output must be missing or an empty directory")
    return resolved


def _tracked_paths(package_format: str) -> list[Path]:
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
        *(str(path) for path in shipped_paths),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"could not enumerate tracked plugin files: {detail}")

    tracked_paths = [Path(path) for path in result.stdout.decode().split("\0") if path]
    if package_format == "portable" and Path("plugin.json") not in tracked_paths:
        # Keep local staging usable before a newly added manifest is staged in Git,
        # while continuing to reject arbitrary untracked runtime files.
        tracked_paths.insert(0, Path("plugin.json"))
    return tracked_paths


def stage(output: Path, package_format: str = DEFAULT_PACKAGE_FORMAT) -> Path:
    """Copy one allowlisted plugin format into *output*."""

    destination = _validate_output(output)
    destination.mkdir(parents=True, exist_ok=True)

    tracked_paths = _tracked_paths(package_format)
    for relative_path in tracked_paths:
        source = REPO_ROOT / relative_path
        target = destination / relative_path
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"staging does not support symlink or non-file: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty external directory named gepa-omni",
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
        destination = stage(args.output, package_format=args.package_format)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"plugin staging error: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
