#!/usr/bin/env python3
"""Check the machine-readable GEPA Omni upstream provenance contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "plugins" / "fleet-optimize" / "skills" / "gepa-omni-skill"
PROVENANCE_PATH = SKILL_ROOT / "PROVENANCE.json"
NOTICE_PATH = SKILL_ROOT / "THIRD_PARTY_NOTICES.md"
SKILL_PATH = SKILL_ROOT / "SKILL.md"


def check() -> list[str]:
    errors: list[str] = []
    try:
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read {PROVENANCE_PATH}: {exc}"]

    upstream = provenance.get("upstream")
    if not isinstance(upstream, dict):
        return ["provenance.upstream must be an object"]
    commit = upstream.get("commit")
    if not isinstance(commit, str) or len(commit) != 40:
        errors.append("provenance.upstream.commit must be a 40-character revision")
    files = provenance.get("files")
    if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
        errors.append("provenance.files must be a list of relative paths")
        files = []

    for relative_path in files:
        source = SKILL_ROOT / relative_path
        if not source.is_file():
            errors.append(f"provenance source file is missing: {relative_path}")
        elif isinstance(commit, str) and commit not in source.read_text(encoding="utf-8"):
            errors.append(f"provenance revision is absent from source header: {relative_path}")

    for path in (NOTICE_PATH, SKILL_PATH):
        text = path.read_text(encoding="utf-8")
        if isinstance(commit, str) and commit not in text:
            errors.append(f"provenance revision is absent from {path.relative_to(REPO_ROOT)}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"provenance error: {error}", file=sys.stderr)
        return 1
    print("GEPA Omni provenance is synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
