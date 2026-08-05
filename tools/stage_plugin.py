#!/usr/bin/env python3
"""Stage the runtime-only GEPA plugin payload outside the checkout."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PLUGIN_NAME = "gepa-omni"
SHIPPED_PATHS = (Path(".codex-plugin"), Path("skills"), Path("LICENSE"))


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


def stage(output: Path) -> Path:
    """Copy only the files required by the installed plugin into *output*."""

    destination = _validate_output(output)
    destination.mkdir(parents=True, exist_ok=True)

    command = [
        "git",
        "-C",
        str(REPO_ROOT),
        "ls-files",
        "-z",
        "--",
        *(str(path) for path in SHIPPED_PATHS),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"could not enumerate tracked plugin files: {detail}")

    tracked_paths = [Path(path) for path in result.stdout.decode().split("\0") if path]
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        destination = stage(args.output)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"plugin staging error: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
