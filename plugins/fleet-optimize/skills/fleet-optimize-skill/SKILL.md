---
name: fleet-optimize-skill
description: >-
  Optimize prompts, code, configurations, schemas, SQL, regexes, and other
  scorable text artifacts with the latest upstream GEPA parallel-proposal
  workflow. Use when a candidate should be improved by P×N sampling,
  concurrent evaluation, Pareto-aware selection, or grouped batch evaluation.
---

# Fleet Optimize

Use Fleet Optimize when the optimization bottleneck benefits from proposing
and evaluating several GEPA mutations in one iteration. This skill targets the
latest upstream GEPA `optimize_anything` API and its P×N proposal strategies.

## Runtime boundary

Fleet Optimize is a thin skill-owned launcher around upstream GEPA. It does
not vendor GEPA or import the `gepa_omni` package or the GEPA Omni skill.
Development and
latest-source setup uses:

```bash
uv sync --project . --group dev
```

When the published package does not yet contain the current parallel-proposal
surface, install the upstream implementation explicitly:

```bash
uv pip install "gepa[full] @ git+https://github.com/gepa-ai/gepa.git@8a2bed96385202f69caaeb5327a843ed2f5ea225"
```

Set `PLUGIN_ROOT` to the installed plugin directory before running the
preflight. For example, use the plugin path supplied by your host:

```bash
export PLUGIN_ROOT="/path/to/fleet-optimize"
```

Before a real run, use the non-interactive preflight:

```bash
uv run python "$PLUGIN_ROOT/skills/fleet-optimize-skill/scripts/preflight.py"
```

The preflight checks the installed upstream surface and the process-scoped
`OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` variables. Never ask a
user to paste an API key into chat. If a model or base URL is missing, ask only
for that missing non-secret value and apply it to the current process; do not
write it to `.env` or another persistent file.

## Candidate and evaluator

The candidate is any text artifact that an evaluator can score: a prompt,
program, configuration, schema, SQL query, regex, plan, or agent instruction.
Return a higher-is-better score and actionable feedback:

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    return grade(output), {
        "output": output,
        "expected": example.get("expected"),
        "error": output.get("error"),
    }
```

Include failures, diffs, traces, partial-credit signals, and resource usage in
the feedback. A bare score leaves the reflection model without a diagnosis.
For stochastic evaluators, average repeated samples inside the evaluator and
return the relevant diagnostics.

Keep training and validation data separate from the held-out `test_set`. The
latest upstream API can score `test_set` after optimization, but the held-out
examples must not be used to select proposals.

## Default parallel workflow

The plugin helper defaults to two parents and two mutations per parent:

Set `PLUGIN_ROOT` to the installed plugin directory, then expose the helper:

```bash
export PYTHONPATH="$PLUGIN_ROOT/skills/fleet-optimize-skill/scripts${PYTHONPATH:+:$PYTHONPATH}"
```

```python
import os

from fleet_optimize import build_config, optimize

config = build_config(
    max_evals=100,
    max_concurrency=16,
    reflection_lm=os.environ["OPENAI_MODEL"],
)

result = optimize(
    seed_candidate=seed,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    test_set=testset,
    objective="Improve the candidate against the evaluator.",
    config=config,
)
```

The default configuration is equivalent to:

```python
from gepa.optimize_anything import OptimizeAnythingConfig
from gepa.strategies.proposal_sampling import PxNSampling
from gepa.strategies.proposal_selection import AllImprovements

config = OptimizeAnythingConfig(
    engine="gepa",
    max_evals=100,
    max_concurrency=16,
    engine_config={
        "engine": {
            "parallel": True,
            "max_workers": 16,
            "sampling_strategy": PxNSampling(p=2, n=2),
            "selection_strategy": AllImprovements(),
        },
        "reflection": {"reflection_lm": os.environ["OPENAI_MODEL"]},
    },
)
```

`PxNSampling(p=2, n=2)` creates four proposal tasks per iteration: two
frontier parents, with two mutations for each parent. The selection strategy
then decides which evaluated improvements enter the candidate pool.

## Choosing the proposal width

- `SameParentSampling(n)` generates `n` mutations from one selected parent.
  Use it to exploit a strong parent with diverse minibatches.
- `IndependentSampling(n)` selects `n` parent/minibatch tasks independently.
  Use it to explore the frontier broadly.
- `PxNSampling(p, n)` selects `p` parents and `n` mutations per parent. Use it
  for a balanced explore/exploit tradeoff.

Selection choices are independent:

- `AllImprovements()` keeps every proposal accepted by the configured
  acceptance criterion.
- `BestImprovement()` keeps only the best improving proposal.
- `TopKImprovements(k)` keeps the `k` strongest improving proposals.

Start with a modest width and set `max_concurrency` high enough to cover the
proposal/evaluation batch without exceeding evaluator capacity or provider
rate limits. Wider iterations consume more calls before the next frontier
update and can overshoot a stop boundary by one iteration.

## Grouped evaluation

Use `batch_evaluator` when the evaluator can submit work as one provider batch,
Ray/Slurm job, Modal dispatch, or other grouped operation:

```python
def batch_evaluate(pairs, opt_states=None):
    results = submit_batch(pairs)
    return [(item.score, item.info) for item in results]


result = optimize(
    seed_candidate=seed,
    batch_evaluator=batch_evaluate,
    dataset=trainset,
    valset=valset,
    objective=objective,
    config=config,
)
```

Return exactly one score or `(score, info)` result for each input pair, in the
same order. If the function accepts `opt_states`, upstream GEPA may provide
aligned warm-start state. Return diagnostics in `info`; grouped evaluation
does not provide per-call stdout capture.

Use ordinary `evaluator` for per-call logging or execution that cannot be grouped.
For shared mutable state, also set `max_concurrency=1` and
`engine_config={"parallel": False}`; singleton calls alone do not serialize evaluation.

## External artifacts and safety

Keep `run_dir` and `output_dir` absolute and outside the repository/plugin
checkout. The Fleet Optimize helper rejects local artifact paths. Use a fresh run
directory for each optimization attempt and retain the result metadata and
held-out scores separately from the selection score.

The model receives the candidate, objective, background, evaluator feedback,
and serialized task data through the upstream GEPA runtime. Do not grant the
reflection model local shell or filesystem access as part of this plugin.

See `references/api.md` for the launcher surface and
`references/parallel-proposals.md` for strategy, batching, and budget details.

For evaluators with shared mutable state, use
`build_config(max_concurrency=1, engine_config={"parallel": False})`.
Singleton `evaluator` calls can otherwise run concurrently.
