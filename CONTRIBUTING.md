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

The engine-capable `gepa[full]` dependency is supplied by the consuming
environment. Do not vendor the GEPA repository or add a deployment-specific
fork URL to the root dependency list.

## Change checklist

- Keep the plugin manifest and skill paths consistent with `gepa-omni` and
  `skills/gepa-omni-skill/`.
- Update focused tests when behavior changes.
- Run `python3 tools/stage_plugin.py --output /tmp/gepa-omni` to verify the
  installable payload when packaging changes. The staging output must contain
  only tracked runtime files.
- Run self-evaluation only with the explicit
  `--allow-candidate-execution` opt-in and a pinned, engine-capable GEPA fork.
- Keep proposer artifacts, evaluation runs, credentials, and generated caches
  out of Git.

## Pull requests

Describe the evaluator or workflow affected, the checks you ran, and any
external runtime or model prerequisites that reviewers need to reproduce it.
