# Contributing to GEPA Omni

## Development setup

Use Python 3.10+ and `uv`:

```bash
uv sync --project . --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

The development group installs the published `gepa[full]==0.1.4` API for the
standalone reflective engine. AutoResearch, Meta-Harness, Best-of-N, and Omni
runtime code are plugin-native and must remain usable without a checkout of a
separate GEPA repository. Do not vendor a second GEPA implementation or add a
deployment-specific dependency URL.

## Change checklist

- Keep the plugin manifest and skill paths consistent with `gepa-omni` and
  `skills/gepa-omni-skill/`.
- Update focused tests when behavior changes.
- Run `python3 tools/stage_plugin.py --output /tmp/gepa-omni` to verify the
  installable payload when packaging changes. The staging output must contain
  only tracked runtime files.
- Run self-evaluation only with the explicit
  `--allow-candidate-execution` opt-in. Keep standalone reflective GEPA tests
  on PyPI 0.1.4 and exercise plugin-native engines through their checked-in
  runtime primitives.
- Keep `skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md` and the pinned MIT
  provenance headers synchronized when native runtime code is adapted.
- Keep proposer artifacts, evaluation runs, credentials, and generated caches
  out of Git.

## Pull requests

Describe the evaluator or workflow affected, the checks you ran, and any
external runtime or model prerequisites that reviewers need to reproduce it.
