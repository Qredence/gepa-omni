# Qredence Optimization Marketplace

This repository publishes one marketplace plugin, **`fleet-optimize` 0.2.0**.
The plugin contains two independent optimization skills, while the repository
also builds a separate Python distribution named **`gepa-omni`**.

| Component | Purpose | Runtime boundary |
| --- | --- | --- |
| `gepa-omni-skill` | Standalone GEPA, fixed portfolio, and adaptive engine selection across GEPA, AutoResearch, Meta-Harness, and Best-of-N. | Uses the plugin-native runtime for portfolio engines and the current upstream GEPA for the standalone reflective engine. |
| `fleet-optimize-skill` | Latest-upstream GEPA optimization with P×N proposal sampling, parallel evaluation, selection strategies, and optional grouped batch evaluation. | Thin launcher around upstream GEPA; it does not import `gepa_omni`. |
| `gepa-omni` Python package | Stable typed API for Python applications that want GEPA Omni orchestration. | Built from `packages/gepa-omni`; it is not an additional marketplace plugin. |

Both skills can optimize text artifacts that an evaluator can score, including
prompts, programs, configurations, schemas, SQL, regular expressions, plans,
and agent instructions. The plugin payload owns both skills, their references,
runtime scripts, manifests, and license. Repository-level development tools,
tests, examples, and the public Python package stay outside the installable
plugin payload.

## Install the plugin

Add the marketplace and install its one plugin:

```bash
codex plugin marketplace add Qredence/gepa-omni
codex plugin add fleet-optimize@Qredence
```

Start a new task after installation so the skills load. For example:

```text
Use $gepa-omni-skill to improve this prompt against my evaluator and report
the held-out score separately.
```

```text
Use $fleet-optimize-skill to optimize this candidate with P×N proposals and
choose a batch evaluator that fits my provider capacity.
```

The marketplace entry is [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json).
It points to `./plugins/fleet-optimize`. Portable plugin consumers use
`plugin.json`; Codex uses `.codex-plugin/plugin.json`. Both manifests include
the same two skills.

## Use the Python package

`gepa-omni` is a separately built Python distribution for embedding the GEPA
Omni API in an application. It is not installed by adding the marketplace
plugin. From this checkout, install the local package with:

```bash
uv pip install ./packages/gepa-omni
```

Then use its public API:

```python
from gepa_omni import OmniConfig, optimize

result = optimize(
    seed_candidate="candidate text",
    evaluator=evaluate,
    dataset=train,
    valset=validation,
    test_set=heldout,
    config=OmniConfig(strategy="auto", max_evals=100),
)
```

See [`packages/gepa-omni/README.md`](packages/gepa-omni/README.md) for package
setup and the evaluator contract. The package declares its own upstream GEPA
dependency in `packages/gepa-omni/pyproject.toml`.

## Choose a workflow

### GEPA Omni

Use `gepa-omni-skill` or the `gepa_omni` Python API for one evaluator contract
across several optimization strategies:

- With a package-only install, `gepa` and `auto` are available; `auto` pilots
  standalone GEPA and Best-of-N. The `omni` fixed portfolio requires the
  GEPA Omni plugin runtime, which the marketplace plugin supplies.
- `strategy="gepa"` runs standalone reflective GEPA.
- `strategy="omni"` runs a fixed portfolio experiment through the plugin
  runtime; the Python package alone raises an error for this strategy.
- `strategy="auto"` pilots eligible engines and allocates the remaining
  budget based on improvement per evaluation. When a useful portfolio does not
  fit the supplied budget, it falls back to standalone GEPA.

GEPA Omni's plugin-native AutoResearch, Meta-Harness, and Best-of-N engines are
independent of the installed GEPA package. The standalone reflective engine
comes from the pinned upstream GEPA API. The adapted native runtime's MIT
provenance is recorded in
[`plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md`](plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md).
See the [GEPA Omni API reference](plugins/fleet-optimize/skills/gepa-omni-skill/references/api.md)
and [engine guide](plugins/fleet-optimize/skills/gepa-omni-skill/references/engines.md).

### Fleet Optimize

Use `fleet-optimize-skill` for the latest upstream GEPA `optimize_anything` parallel
proposal workflow. The helper defaults to `PxNSampling(p=2, n=2)` and
`AllImprovements()` and returns the native upstream result. A minimal call is:

```python
from fleet_optimize import build_config, optimize

config = build_config(
    max_evals=100,
    max_concurrency=16,
    reflection_lm="your-reflection-model",
)
result = optimize(
    seed_candidate=seed,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    test_set=heldout,
    objective="Improve the candidate against the evaluator.",
    config=config,
)
```

Set `PLUGIN_ROOT` to the installed `fleet-optimize` directory and add the
skill's `scripts` directory to `PYTHONPATH` when importing `fleet_optimize`
from a separate Python application. The [Fleet Optimize skill](plugins/fleet-optimize/skills/fleet-optimize-skill/SKILL.md),
[API reference](plugins/fleet-optimize/skills/fleet-optimize-skill/references/api.md),
and [parallel proposal guide](plugins/fleet-optimize/skills/fleet-optimize-skill/references/parallel-proposals.md)
cover configuration, selection, batching, and concurrency.

## Evaluators and held-out data

Both workflows expect higher-is-better scores and benefit from concrete
feedback:

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    return grade(output), {
        "output": output,
        "expected": example.get("gold"),
        "error": output.get("error"),
    }
```

Keep training, validation, and held-out data separate. Training examples
provide proposal feedback; validation data supports candidate selection. For
GEPA Omni, `test_set` is scored after optimization and is not used in engine
selection or allocation. Fleet Optimize passes `test_set` to upstream GEPA for
its final held-out reporting. Do not use held-out results to revise a candidate
and then report those same examples as an untouched test set.

## Provider setup and data handling

GEPA Omni uses process-scoped OpenAI-compatible Chat Completions settings:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

`OPENAI_MODEL` is authoritative for GEPA Omni. The historical `agent_backend`
name remains a compatibility alias for `runner_profile`; these profile labels
do not launch Codex, Pi, or Claude CLIs or grant local shell and filesystem
tools. For the official OpenAI endpoint, GEPA Omni may use `OPENAI_API_KEY`.
For other providers, configure `GEPA_OMNI_API_KEY` or set
`GEPA_OMNI_API_KEY_ENV` to the name of a provider-specific environment
variable; GEPA Omni does not send `OPENAI_API_KEY` to another endpoint. Fleet
Optimize follows upstream GEPA's `OPENAI_*` environment contract and does not
apply GEPA Omni's provider-key isolation. Use a key scoped appropriately for
the endpoint configured for that workflow.

For GEPA Omni, remote endpoints require HTTPS; HTTP is allowed only for
loopback, and private addresses require explicit opt-in. Interactive setup may
request a missing model or base URL for the current process; it never asks for
a key in chat or writes prompted values to `.env`. Configure keys through the
environment or a secret manager. Preflight remains non-interactive and makes
no model call unless `--test-lm` is supplied. Both workflows can send the
candidate, evaluator inputs, and related prompts to the configured provider.
Keep `run_dir` and `output_dir` absolute and outside the checkout.

Read [`PRIVACY.md`](PRIVACY.md) for the project's data-handling summary and
the [provider guide](plugins/fleet-optimize/skills/gepa-omni-skill/references/providers.md)
for endpoint configuration.

## Development and packaging

Use Python 3.10+ and `uv`. The development group and Python package pin
upstream GEPA to `8a2bed96385202f69caaeb5327a843ed2f5ea225` because the
engine-pluggable parallel-proposal API used here is ahead of a published
release. Update the revision in the root and package `pyproject.toml` files
together, then refresh the lockfile:

```bash
uv lock --upgrade-package gepa
```

Run the repository checks:

```bash
uv sync --locked --group dev
uv run coverage run --branch --source=packages/gepa-omni/src,plugins/fleet-optimize/skills/gepa-omni-skill/scripts,benchmarks/gepa-omni -m pytest -q
uv run coverage report --fail-under=50
uv run python tools/check_gepa_omni_provenance.py
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Run the GEPA Omni preflight from the repository root before a live run. It is
non-interactive and makes no model call unless `--test-lm` is supplied:

```bash
uv run python plugins/fleet-optimize/skills/gepa-omni-skill/scripts/preflight.py --engine gepa
uv run python plugins/fleet-optimize/skills/gepa-omni-skill/scripts/preflight.py --engine omni
```

For Fleet Optimize, set `PLUGIN_ROOT` to the installed plugin directory and
run its preflight. It checks required upstream symbols and fails when the API
is incompatible instead of silently selecting an older one:

```bash
uv run python "$PLUGIN_ROOT/skills/fleet-optimize-skill/scripts/preflight.py"
```

For plugin payload validation, set `PLUGIN_CREATOR` to the checkout of the
plugin-creator skill and run:

```bash
uv run python "$PLUGIN_CREATOR/scripts/validate_plugin.py" plugins/fleet-optimize
```

To stage the tracked runtime payload, use an external empty directory named
`fleet-optimize`:

```bash
stage_parent="$(mktemp -d)"
uv run python tools/stage_plugin.py --format portable --output "$stage_parent/fleet-optimize"

codex_parent="$(mktemp -d)"
uv run python tools/stage_plugin.py --format codex --output "$codex_parent/fleet-optimize"
```

Staging reads file bytes from the Git index. Portable output contains
`plugin.json`, `skills/`, and `LICENSE`; Codex output contains
`.codex-plugin/plugin.json`, `skills/`, and `LICENSE`. Development tools,
tests, lockfiles, root marketplace metadata, and untracked files are excluded.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the change checklist.

## Examples and benchmarks

[`examples/README.md`](examples/README.md) explains the support-ticket prompt
optimization example, its usage accounting, and the included easy and hard
datasets. [`benchmarks/gepa-omni/README.md`](benchmarks/gepa-omni/README.md)
documents the credential-free schema smoke fixture; it does not run engines or
measure performance.

## License and attribution

The repository and installable plugin are distributed under the [MIT License](LICENSE).
The plugin builds on the MIT-licensed [GEPA project](https://github.com/gepa-ai/gepa).
See the GEPA Omni [third-party notice](plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md)
for the adapted runtime's scope and attribution.
