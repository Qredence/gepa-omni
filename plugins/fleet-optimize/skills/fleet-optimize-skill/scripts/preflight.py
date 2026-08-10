#!/usr/bin/env python3
"""Check latest GEPA and provider configuration for Fleet Optimize."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fleet_optimize import FleetOptimizeCompatibilityError, describe_compatibility  # noqa: E402


def _provider_diagnostics() -> dict[str, bool]:
    return {
        "OPENAI_BASE_URL": bool(os.environ.get("OPENAI_BASE_URL", "").strip()),
        "OPENAI_MODEL": bool(os.environ.get("OPENAI_MODEL", "").strip()),
        "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    report: dict[str, object] = {"provider": _provider_diagnostics()}
    problems: list[str] = []
    try:
        report["gepa"] = describe_compatibility()
    except FleetOptimizeCompatibilityError as exc:
        problems.append(str(exc))
        report["gepa"] = {"compatible": False, "error": str(exc)}

    missing = [name for name, configured in report["provider"].items() if not configured]  # type: ignore[union-attr]
    if missing:
        problems.append("missing provider variables: " + ", ".join(missing))

    print(json.dumps({"ok": not problems, **report, "problems": problems}, indent=2, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
