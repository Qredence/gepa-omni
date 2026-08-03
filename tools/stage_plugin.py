#!/usr/bin/env python3
"""Stage the runtime-only GEPA plugin payload outside the checkout."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PLUGIN_NAME = "fleet-omni"
SHIPPED_PATHS = (Path(".codex-plugin"), Path("skills"), Path("LICENSE"))


def _ignore_macos_metadata(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name == ".DS_Store"]


def _validate_output(output: Path) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("--output must be outside the development checkout")
    if resolved.name != CANONICAL_PLUGIN_NAME:
        raise ValueError(
            f"--output directory must be named {CANONICAL_PLUGIN_NAME!r}"
        )
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

    for relative_path in SHIPPED_PATHS:
        source = REPO_ROOT / relative_path
        target = destination / relative_path
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=_ignore_macos_metadata,
            )
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError(f"required plugin path is missing: {relative_path}")
    return destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty external directory named fleet-omni",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        destination = stage(args.output)
    except (FileNotFoundError, ValueError) as exc:
        print(f"plugin staging error: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
