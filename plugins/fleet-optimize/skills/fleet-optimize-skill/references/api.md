# Fleet Optimize API

Fleet Optimize follows the latest upstream `gepa.optimize_anything` contract.
The helper in `scripts/fleet_optimize.py` adds safe defaults for the parallel
proposal workflow and returns the native upstream result object.

## `build_config`

```python
from fleet_optimize import build_config

config = build_config(
    parents=2,
    mutations=2,
    max_evals=100,
    max_token_cost=None,
    max_concurrency=16,
    run_dir="/tmp/fleet-optimize-run",
    output_dir="/tmp/fleet-optimize-output",
    reflection_lm="openai/gpt-5-mini",
)
```

The helper creates the current upstream `OptimizeAnythingConfig` with nested
engine and reflection option mappings:

- `engine.parallel=True`
- `engine.max_workers=max_concurrency`
- `engine.sampling_strategy=PxNSampling(parents, mutations)`
- `engine.selection_strategy=AllImprovements()`
- `reflection.reflection_lm=reflection_lm`

Pass `sampling_strategy` or `selection_strategy` to replace those defaults.
Additional `engine_config` and `reflection_config` mappings are merged without
modifying the caller's dictionaries.

## `optimize`

```python
from fleet_optimize import optimize

result = optimize(
    seed_candidate=seed_candidate,
    evaluator=evaluator,
    batch_evaluator=batch_evaluator,
    dataset=dataset,
    valset=valset,
    test_set=test_set,
    objective=objective,
    background=background,
    config=config,
)
```

`config` is optional. When omitted, `optimize()` calls `build_config()` with
the default P×N workflow. When supplied, it must be the latest upstream
`OptimizeAnythingConfig`; the helper does not silently convert legacy
`GEPAConfig` objects.

The native arguments are passed through unchanged:

- `evaluator` may be omitted when `batch_evaluator` is supplied.
- `dataset` is used for proposal feedback.
- `valset` is used for selection/generalization.
- `test_set` is held out for final reporting by the latest upstream API.
- `run_dir` stores engine state; `output_dir` stores evaluation artifacts.

## Compatibility

Run:

```bash
python3 skills/fleet-optimize-skill/scripts/preflight.py
```

The compatibility gate requires the latest engine-pluggable surface:

- `OptimizeAnythingConfig`
- `optimize_anything(..., batch_evaluator=...)`
- `PxNSampling`, `SameParentSampling`, and `IndependentSampling`
- `AllImprovements`, `BestImprovement`, and `TopKImprovements`

If any required symbol is missing, install the newest upstream package or Git
revision and rerun preflight. No old API fallback is attempted.
