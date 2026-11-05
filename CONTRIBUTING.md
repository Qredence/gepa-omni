# Contributing to the Qredence Optimization Marketplace

## Development setup

Use Python 3.10+ and `uv`:

```bash
uv sync --locked --group dev
uv run coverage run --branch --source=packages/gepa-omni/src,plugins/fleet-optimize/skills/gepa-omni-skill/scripts,benchmarks/gepa-omni -m pytest -q
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

Do not vendor a second GEPA implementation. `fleet-optimize` is the only
installable plugin. Its `fleet-optimize-skill` must not import `gepa_omni`
or the GEPA Omni skill, and `gepa-omni-skill` must not import
`fleet_optimize`. The public `gepa_omni` package owns broad orchestration;
the GEPA Omni skill owns the plugin-native runtime.

## Change checklist

- Keep `plugins/fleet-optimize/` installable, with both `plugin.json` and
  `.codex-plugin/plugin.json`, and with both `fleet-optimize-skill` and
  `gepa-omni-skill` in its payload.
- Keep the plugin `LICENSE` and both `skills/<skill>/` payloads in sync
  with its manifests and marketplace entry.
- Update focused tests when behavior changes, including marketplace source
  paths, skill discovery, staging, and latest-GEPA compatibility diagnostics.
- Stage both formats when packaging changes:

  ```bash
  python3 tools/stage_plugin.py --plugin fleet-optimize --format portable --output /tmp/fleet-portable/fleet-optimize
  python3 tools/stage_plugin.py --plugin fleet-optimize --format codex --output /tmp/fleet-codex/fleet-optimize
  ```

  Outputs must be external, empty, and named `fleet-optimize`. The staging
  tool copies only git-index runtime files and excludes tests, tools,
  lockfiles, root metadata, credentials, and untracked files.
- Run the plugin validation tooling for the payload.
- Run self-evaluation only with the explicit
  `--allow-candidate-execution` opt-in. Keep optimizer artifacts outside the
  repository checkout.
- Preserve the process-scoped `OPENAI_BASE_URL`, `OPENAI_MODEL`, and
  `OPENAI_API_KEY` boundary. Never persist credentials or request API keys in
  chat.
- Keep `plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md` and
  `PROVENANCE.json` synchronized with the pinned MIT provenance headers when
  native runtime code is adapted. Run the provenance checker before packaging.

## Pull requests

Describe the plugin, evaluator, or workflow affected, the checks you ran, the
resolved GEPA revision, and any external runtime or model prerequisites that
reviewers need to reproduce it. Do not include provider keys, optimizer state,
run artifacts, or generated caches.
