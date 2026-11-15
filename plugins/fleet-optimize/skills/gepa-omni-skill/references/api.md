# GEPA Omni API

The latest upstream GEPA supplies the standalone reflective engine. GEPA Omni
adds the typed public package and isolated native adapters around the upstream GEPA API.

Interactive setup keeps non-secret values in the current process, never
requests an API key, and leaves preflight non-interactive. API keys are never requested. If configuration is
missing, setup asks only for non-secret model and URL values.

The plugin-native engines are `autoresearch`, `meta_harness`, and `best_of_n`;
the fixed portfolio is plugin-native orchestration around the latest upstream
GEPA API.

The supported plugin API is the separately packaged `gepa_omni` module:

```python
from gepa_omni import OmniConfig, optimize, run_optimization

result = optimize(
    seed_candidate="candidate",
    evaluator=evaluate,
    dataset=train,
    valset=validation,
    test_set=test,
    config=OmniConfig(strategy="auto", max_evals=100),
)
```

`optimize` is evaluator-first. `run_optimization` accepts the same data as a
task mapping for integrations that already normalize evaluators. Both return a
stable result view with `best_candidate`, `best_score`, `total_evals`,
`metadata`, and the engine-specific `raw` result. Upstream `optimize_anything`
now returns a single unified `gepa.core.result.GEPAResult` on every run: the
lean accessors (`best_candidate`, `best_score`, `total_evals`, `eval_log`,
`metadata`) are real properties, the candidate pool lives on the result
(`candidates`, `best_idx`, `val_aggregate_scores`), and `metadata` no longer
nests a `gepa_result` object. `total_evals` is the eval-server call count
(`budget.used`), not `total_metric_calls`; the legacy `GEPAConfig` path
bypasses the eval server, so there `total_evals` falls back to
`total_metric_calls`.

## Configuration

`OmniConfig` fields include:

- `strategy`: `gepa`, `omni`, or `auto`;
- `max_evals` and/or `max_token_cost`;
- `model` and `runner_profile`;
- `max_concurrency`, `stop_at_score`, and `sandbox`;
- external `run_dir` and `output_dir`;
- input/output token pricing when a dollar cap is used.

`runner_profile` replaces the historical `agent_backend` name. The historical
name remains a compatibility alias for one release. It does not select a CLI
or grant the model shell/filesystem access.

## Held-out scoring

`test_set` is withheld from every selection and allocation decision. It is
scored only after the selected candidate is produced. The direct upstream
`optimize_anything` call has no allocation-aware test phase, so callers should
perform held-out scoring after that call when using the upstream API directly.

## Direct upstream GEPA

When no portfolio is needed, the upstream reflective API remains available:

```python
from gepa.optimize_anything import (
    EngineConfig,
    GEPAConfig,
    ReflectionConfig,
    optimize_anything,
)

result = optimize_anything(
    seed_candidate="candidate text",
    evaluator=evaluate,
    objective="Improve the candidate.",
    config=GEPAConfig(
        engine=EngineConfig(max_metric_calls=20),
        reflection=ReflectionConfig(reflection_lm=model),
    ),
)
```

`ReflectionConfig` also accepts an optional
`reflective_dataset_enricher` hook: it receives the current candidate,
evaluation batch, components to update, and the adapter's reflective dataset
(keyword-only), and returns an enriched dataset just before the reflective
proposal. Set it on `ReflectionConfig` (also keyword-only on the top-level
`gepa.optimize`, which does not apply to `optimize_anything`'s direct
signature); use it for additive feedback (for example, independent trace
reviewers) without wrapping the adapter.

The package and plugin adapter preserve the external `run_dir`/`output_dir`
boundary. Do not import the internal `omni_pipeline` module in user code.

Compatibility names retained for one release include `agent_backend`,
`agent_model`, `codex_model`, `pi_model`, `codex_command`, `pi_command`,
`codex_input_cost_per_million`, `codex_output_cost_per_million`, and
`sandbox=True`. They map to the shared runner contract and do not launch a
provider CLI. The upstream legacy surface includes `OptimizeAnythingConfig`,
`GEPAConfig`, `EngineConfig.max_metric_calls`, `EngineConfig(max_metric_calls=...)`, `ReflectionConfig`, and `stop_callbacks`; the
plugin wrapper keeps `task["test_set"]` outside optimization and scores it only
for held-out reporting.
