# `gepa-omni`

`gepa-omni` is the public Python distribution for using GEPA Omni
orchestration from an application. It is packaged separately from the
`fleet-optimize` marketplace plugin, which bundles the user-facing
`gepa-omni-skill` and `fleet-optimize-skill` runtimes.

## Install

From this repository, install the local package with:

```bash
uv pip install ./packages/gepa-omni
```

The package requires Python 3.10+ and declares the validated upstream GEPA Git
revision in its project metadata. To install from another project, publish or
build this directory as a Python distribution and install that distribution;
installing the marketplace plugin alone does not add the `gepa_omni` module to
an application's Python environment.

## Optimize a candidate

```python
from gepa_omni import OmniConfig, optimize


def evaluate(candidate: str, example: dict) -> tuple[float, dict]:
    output = run_system(candidate, example)
    return grade(output), {"output": output, "expected": example.get("expected")}


result = optimize(
    seed_candidate="candidate text",
    evaluator=evaluate,
    dataset=train_examples,
    valset=validation_examples,
    test_set=heldout_examples,
    config=OmniConfig(strategy="auto", max_evals=100),
)

print(result.best_candidate, result.best_score)
print(result.metadata.get("heldout_score"))
```

The evaluator returns a higher-is-better score, optionally paired with a
diagnostic dictionary for the proposer. `dataset` supplies proposal feedback
and `valset` supports candidate selection. `test_set` is scored after the
selected candidate is produced; it does not influence engine selection or
budget allocation. `result` exposes `best_candidate`, `best_score`,
`total_evals`, `metadata`, and the engine-specific `raw` result.

## Choose a strategy

- `gepa` runs standalone reflective GEPA.
- `omni` runs the fixed GEPA, AutoResearch, and Meta-Harness portfolio and
  continuation experiment.
- `auto` pilots eligible engines, then allocates the remaining budget based
  on improvement per evaluation. A budget too small for a useful portfolio
  falls back to standalone GEPA.

Set `max_evals` and/or `max_token_cost` in `OmniConfig`. A token cost cap
requires both input and output pricing rates. Keep `run_dir` and `output_dir`
absolute and outside the repository checkout.

See the [repository guide](../../README.md) and the
[GEPA Omni skill](../../plugins/fleet-optimize/skills/gepa-omni-skill/SKILL.md)
for setup, provider, security, and engine details.
