#!/usr/bin/env python3
"""Developer entrypoint for the GEPA Omni project's self-evaluation harness."""

import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    tools_dir = parent / "tools"
    if (tools_dir / "gepa_self_evaluate.py").is_file():
        sys.path.insert(0, str(tools_dir))
        break
else:  # pragma: no cover - only reached when the project is copied incompletely
    raise RuntimeError("tools/gepa_self_evaluate.py was not found")

from gepa_self_evaluate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
