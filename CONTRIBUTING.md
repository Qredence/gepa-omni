# Contributing to the Qredence Optimization Marketplace

## Development setup

Use Python 3.10+ and `uv`:

```bash
uv sync --locked --group dev
uv run coverage run --branch --source=packages/gepa-omni/src,plugins/gepa-omni/skills/gepa-omni-skill/scripts,benchmarks/gepa-omni -m pytest -q
uv run coverage report --fail-under=50
uv run python tools/check_gepa_omni_provenance.py
uv run ruff check .
uv run ruff format --check .
git diff --check
```

The development group follows the latest upstream GEPA implementation. The
dependency is intentionally not a permanent exact-version pin: the
project uses upstream Git while the parallel-proposal API is ahead of a
published release, and `uv.lock` records the exact revision used by CI.
Refresh it deliberately with:

```bash
uv lock --upgrade-package gepa
uv sync --locked --group dev
```

Do not vendor a second GEPA implementation. `fleet-optimize` must remain
independently installable as a plugin payload and must not import
`gepa-omni` internals. `gepa-omni` owns its broad orchestration and its
plugin-native runtime.

## Change checklist

- Keep `plugins/gepa-omni/` and `plugins/fleet-optimize/` independently
  installable, with both `plugin.json` and `.codex-plugin/plugin.json`.
- Keep each plugin's `LICENSE` and `skills/<plugin-skill>/` payload in sync
  with its manifests and marketplace entry.
- Update focused tests when behavior changes, including marketplace source
  paths, skill discovery, staging, and latest-GEPA compatibility diagnostics.
- Stage both formats when packaging changes:

  ```bash
  python3 tools/stage_plugin.py --plugin gepa-omni --format portable --output /tmp/gepa-omni
  python3 tools/stage_plugin.py --plugin fleet-optimize --format codex --output /tmp/fleet-optimize
  ```

  Outputs must be external, empty, and named after the selected plugin. The
  staging tool copies only tracked runtime files and excludes tests, tools,
  lockfiles, root metadata, credentials, and arbitrary untracked files.
- Run the plugin validation tooling for both payloads.
- Run self-evaluation only with the explicit
  `--allow-candidate-execution` opt-in. Keep optimizer artifacts outside the
  repository checkout.
- Preserve the process-scoped `OPENAI_BASE_URL`, `OPENAI_MODEL`, and
  `OPENAI_API_KEY` boundary. Never persist credentials or request API keys in
  chat.
- Keep `plugins/gepa-omni/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md` and
  `PROVENANCE.json` synchronized with the pinned MIT provenance headers when
  native runtime code is adapted. Run the provenance checker before packaging.

## Pull requests

Describe the plugin, evaluator, or workflow affected, the checks you ran, the
resolved GEPA revision, and any external runtime or model prerequisites that
reviewers need to reproduce it. Do not include provider keys, optimizer state,
run artifacts, or generated caches.
