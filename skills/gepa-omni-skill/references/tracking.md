# Experiment tracking

Chat Completions proposer calls retain per-proposal input, output, and response
files under the configured run directory. When input/output USD-per-million
rates are supplied, JSON token telemetry is also accumulated into `usd_cost`.
This is separate from optional W&B or MLflow tracking.

The GEPA engine logs to **Weights & Biases** and **MLflow** through
`TrackingConfig`. Pass it as a top-level field on the published `GEPAConfig`:

Set `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` before running; the
model name in the example is read from `OPENAI_MODEL`.

```python
import os
from gepa.optimize_anything import (
    EngineConfig,
    GEPAConfig,
    ReflectionConfig,
    TrackingConfig,
    optimize_anything,
)

result = optimize_anything(
    seed_candidate=SEED,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    config=GEPAConfig(
        engine=EngineConfig(max_metric_calls=300, run_dir="external-runs/my-gepa-run"),
        reflection=ReflectionConfig(reflection_lm=os.environ["OPENAI_MODEL"]),
        tracking=TrackingConfig(
            use_wandb=True,
            wandb_init_kwargs={
                "project": "my-gepa-run",
                "name": "experiment-1",
            },
        ),
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

For programmatic observation beyond metric logging, pass `callbacks` on
`GEPAConfig`. A configured `EngineConfig.run_dir` also retains GEPA's
run log, candidate state, and candidate-tree artifacts.

Tracking is currently an engine-specific feature; AutoResearch and
Meta-Harness persist their own upstream workspace/session artifacts under
their configured run directory.

See the official guide for the authoritative list of fields and behaviors:
<https://gepa-ai.github.io/gepa/guides/experiment-tracking/>.
