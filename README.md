# Qredence Optimization Marketplace

This repository is a small marketplace for the Qredence optimization plugin.
It contains one installable payload, **`fleet-optimize` 0.2.0**, with two skills:

- **`gepa-omni-skill`** — standalone, fixed-portfolio, and adaptive
  orchestration across the standalone reflective GEPA, AutoResearch, Meta-Harness, and
  Best-of-N.
- **`fleet-optimize-skill`** — focused latest-upstream GEPA optimization with
  P×N proposal sampling, concurrent evaluation, selection strategies, and
  optional grouped batch evaluation.

Both skills optimize any artifact that can be serialized and scored: prompts,
programs, configurations, schemas, SQL, regular expressions, plans, or agent
instructions. The repository itself contains marketplace metadata, shared
development tooling, tests, and documentation. The plugin owns its manifests,
license, skills, references, and runtime payload. The public `gepa_omni`
library stays in `packages/gepa-omni` and is not imported by
`fleet-optimize-skill`.

## Install from the Qredence marketplace

Add the repository once, then install the plugin:

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

The marketplace definition is [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json).
Its local source resolves to `./plugins/fleet-optimize`.

## GEPA dependency policy

The latest upstream GEPA implementation is the source of truth for the
parallel-proposal API. Development uses the upstream Git repository while the
required API is ahead of a published release:

```toml
"gepa[full] @ git+https://github.com/gepa-ai/gepa.git@8a2bed96385202f69caaeb5327a843ed2f5ea225"
```

The package and lockfile require this exact upstream revision because the
published 0.1.4 distribution does not expose the engine-pluggable
`OptimizeAnythingConfig` surface used by the plugin. Refresh the revision
intentionally when the API contract is revalidated:

```bash
uv lock --upgrade-package gepa
uv sync --locked --group dev
```

The [Fleet Optimize preflight](plugins/fleet-optimize/skills/fleet-optimize-skill/scripts/preflight.py)
reports the installed version/revision, configuration parameters, proposal
sampling and selection symbols, and `batch_evaluator` availability. It fails
with an upgrade message if the installed API is incompatible; it never selects
the old API silently.

## Plugin catalog

### `gepa-omni-skill`

GEPA Omni is the adaptive orchestration skill. It uses one evaluator contract
across standalone GEPA and its plugin-native AutoResearch, Meta-Harness, and
Best-of-N engines. `strategy="auto"` pilots eligible engines and allocates the
remaining budget to the strongest option. `strategy="omni"` is an explicit
fixed-portfolio experiment.

The plugin-native runtime is independent of the installed GEPA package. Its
MIT provenance is recorded in
[`plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md`](plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md).
Read the [GEPA Omni API reference](plugins/fleet-optimize/skills/gepa-omni-skill/references/api.md)
for engine-specific settings and result metadata.

Python callers install the isolated package and import its stable API:

```bash
uv pip install packages/gepa-omni
```

```python
from gepa_omni import OmniConfig, optimize
```

### `fleet-optimize-skill`

Fleet Optimize is the focused latest upstream GEPA path. Its default is:

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

The helper constructs the current upstream `OptimizeAnythingConfig` with
`PxNSampling(p=2, n=2)` and `AllImprovements()`. Pass
`SameParentSampling`, `IndependentSampling`, `BestImprovement`, or
`TopKImprovements` explicitly when the search shape calls for it. Use
`batch_evaluator` for provider batch APIs, Ray, Slurm, Modal, or another
grouped execution system. The skill returns the native upstream result and
does not import `gepa_omni`.

See the [Fleet Optimize skill](plugins/fleet-optimize/skills/fleet-optimize-skill/SKILL.md),
[API reference](plugins/fleet-optimize/skills/fleet-optimize-skill/references/api.md),
and [parallel-proposals guide](plugins/fleet-optimize/skills/fleet-optimize-skill/references/parallel-proposals.md).

## Evaluator conventions

The two skills use similar higher-is-better evaluator conventions, but keep
independent runtime and provider contracts. GEPA Omni uses:

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    return grade(output), {
        "output": output,
        "expected": example.get("gold"),
        "error": output.get("error"),
    }
```

Keep training data, validation data, and held-out reporting data separate.
`valset` is for selection/generalization; `test_set` is reporting-only. A
feedback-rich evaluator should include failures, diffs, traces, partial-credit
signals, and resource usage rather than only a scalar.

GEPA Omni uses this process-scoped provider configuration:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

`OPENAI_MODEL` is authoritative for GEPA Omni. Its public API uses
`runner_profile` for compatibility metadata; the historical `agent_backend`
name is accepted for one release. These profiles do not launch Codex, Pi, or
Claude CLIs. GEPA Omni's current runner receives no local shell or filesystem
tools.

For the official OpenAI endpoint, `OPENAI_API_KEY` is allowed. For another
endpoint, configure `GEPA_OMNI_API_KEY` or a provider-specific variable;
`OPENAI_API_KEY` is never sent there. Remote endpoints require HTTPS, HTTP is
allowed only for loopback, and private addresses require explicit opt-in.

Interactive setup may ask for a missing model or base URL and applies it only
to the current process. It never asks the user to paste `OPENAI_API_KEY` into
chat, does not write prompted values to `.env`, and expects the key from the
environment or a secret manager. Preflight remains non-interactive and reports
which process variables are missing.

Keep `run_dir` and `output_dir` absolute and outside the repository checkout.
Optimizer state, provider traces, credentials, and run artifacts do not belong
in this repository.

## Portable and Codex staging

The staging tool emits the tracked runtime payload for `fleet-optimize`.
The output must be external, empty, and named `fleet-optimize`:

```bash
stage_parent="$(mktemp -d)"
python3 tools/stage_plugin.py \
  --format portable \
  --output "$stage_parent/fleet-optimize"

codex_parent="$(mktemp -d)"
python3 tools/stage_plugin.py \
  --format codex \
  --output "$codex_parent/fleet-optimize"
```

Portable output contains `plugin.json`, `skills/`, and `LICENSE`. Codex output
contains `.codex-plugin/plugin.json`, `skills/`, and `LICENSE`. Both skills
are included. Development tests, tools, lockfiles, root metadata, and
arbitrary untracked runtime files are excluded. File bytes come from the git
index, so unstaged edits do not ship.

## Repository layout

```text
plugins/
  fleet-optimize/
    plugin.json
    .codex-plugin/plugin.json
    LICENSE
    skills/fleet-optimize-skill/
    skills/gepa-omni-skill/
.agents/plugins/marketplace.json
README.md
CONTRIBUTING.md
pyproject.toml
uv.lock
packages/gepa-omni/
tests/
tools/
```

The plugin has distinct portable and Codex manifests with shared metadata.
The root marketplace metadata is not copied into the installable payload.

## Development and validation

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

Validate the payload with the plugin tooling before distribution:

```bash
python3 /Users/zocho/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/fleet-optimize
```

Run the GEPA Omni preflight with the approved endpoint key in the current
process; it does not make a model call unless `--test-lm` is supplied. The
Fleet Optimize skill retains its independent provider contract.

## Attribution and license

The repository and the installable payload are distributed under the
[MIT License](LICENSE). The plugin builds on the original
[GEPA Anything project](https://github.com/gepa-ai/gepa), also MIT licensed.
The adapted `gepa-omni` native runtime is pinned to commit
[`8a2bed96385202f69caaeb5327a843ed2f5ea225`](https://github.com/gepa-ai/gepa/tree/8a2bed96385202f69caaeb5327a843ed2f5ea225);
see its [third-party notice](plugins/fleet-optimize/skills/gepa-omni-skill/THIRD_PARTY_NOTICES.md)
for scope and attribution.
