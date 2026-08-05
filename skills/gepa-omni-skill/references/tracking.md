# Experiment tracking

Codex proposer calls retain per-proposal input, output, event, and stderr
files under the configured run directory. This is separate from optional W&B
or MLflow tracking; provider-specific Codex pricing is not converted into a
USD metric.

The GEPA engine logs to **Weights & Biases** and **MLflow** through
`TrackingConfig`. Pass the GEPA configuration through
`OptimizeAnythingConfig.engine_config`:

```python
from gepa.optimize_anything import (
    EngineConfig,
    OptimizeAnythingConfig,
    ReflectionConfig,
    TrackingConfig,
    optimize_anything,
)

result = optimize_anything(
    seed_candidate=SEED,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    config=OptimizeAnythingConfig(
        engine="gepa",
        max_evals=300,
        run_dir="external-runs/my-gepa-run",
        output_dir="external-runs/my-gepa-run/output",
        engine_config={
            "engine": EngineConfig(),
            "reflection": ReflectionConfig(reflection_lm="anthropic/claude-sonnet-4-6"),
            "tracking": TrackingConfig(
                use_wandb=True,
                wandb_init_kwargs={
                    "project": "my-gepa-run",
                    "name": "experiment-1",
                },
            ),
        },
    ),
)
```

## `TrackingConfig` fields

- `use_wandb`, `wandb_api_key`, `wandb_init_kwargs`, `wandb_attach_existing`, `wandb_step_metric`
- `use_mlflow`, `mlflow_tracking_uri`, `mlflow_experiment_name`, `mlflow_attach_existing`
- `key_prefix` — prepended to every logged key/name
- `logger` — a custom `LoggerProtocol` for plain-text log lines

## What gets logged

- **scalars** — validation score, best score, total evaluations, and cost
- **tables** — candidates, proposals, validation scores, and Pareto front
- **run summary** — seed and best-found component text
- **interactive candidate-tree HTML** when supported by the GEPA engine

## Attaching to an existing run

If you've already called `wandb.init()` (or started an MLflow run) yourself —
for example, from a parent script that also logs other things — set
`wandb_attach_existing=True` or `mlflow_attach_existing=True`. The tracker then
logs into your active run without calling `init()` or `finish()`.

When the host loop manages its own W&B step counter, also set
`wandb_step_metric` so GEPA's iteration steps do not collide with the host's
counter.

## Custom hooks

For programmatic observation beyond metric logging, pass `callbacks` inside
the GEPA `engine_config` mapping. A configured `run_dir` also retains GEPA's
run log, candidate state, and candidate-tree artifacts.

Tracking is currently an engine-specific feature; AutoResearch and
Meta-Harness persist their own upstream workspace/session artifacts under
their configured run directory.

See the official guide for the authoritative list of fields and behaviors:
<https://gepa-ai.github.io/gepa/guides/experiment-tracking/>.
