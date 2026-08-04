# Engine-pluggable `optimize_anything`

This plugin uses the current upstream `gepa.optimize_anything` launcher. Its
local `pyproject.toml` contains only plugin development tools; follow the
canonical [local setup](../SKILL.md#local-setup) and keep GEPA external.

For this API path, select `--engine gepa --agent-backend pi` when running the
canonical preflight command.

Use the pinned maintained fork until the generic agent-runner extension is
available in the release you have selected. Neither the repository root nor
the plugin project adds GEPA as a dependency, because the required fork URL
and commit are deployment-specific.

The launcher keeps the task and evaluator contract stable while allowing the
optimization engine to change.

## Core signature

```python
from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything

result = optimize_anything(
    seed_candidate: str | dict[str, str] | None = None,
    *,
    evaluator=None,
    batch_evaluator=None,
    dataset=None,
    valset=None,
    test_set=None,
    objective=None,
    background=None,
    config=OptimizeAnythingConfig(...),
)
```

`evaluator` receives `(candidate)` in single-task mode or
`(candidate, example)` when `dataset`, `valset`, or `test_set` is provided. It
returns a higher-is-better score or `(score, info)`. The `info` dictionary is
Actionable Side Information: include concrete failures, outputs, diffs, and
partial-credit signals so the selected engine can improve the candidate.

`batch_evaluator` is an optional grouped form for evaluation systems that can
score several candidate/example pairs in one call.

## Optimization modes

| mode | configuration | evaluator call |
| --- | --- | --- |
| Single-task | `dataset=None, valset=None` | `evaluator(candidate)` |
| Multi-task | `dataset=[...]`, `valset=None` | `evaluator(candidate, example)` |
| Generalization | `dataset=[...]`, `valset=[...]` | train on `dataset`, select on `valset` |

`test_set` is optional held-out data. It is evaluated outside the optimization
loop and is not shown to the engine. Use it for final unbiased reporting.

The GEPA engine supports a dictionary seed for co-optimizing named
components. `autoresearch`, `meta_harness`, and `best_of_n` treat a dictionary
seed as one text candidate; use a string seed with those engines.

## Engine decision table

| engine | use it for | agent/backend notes |
| --- | --- | --- |
| `gepa` | Pareto/reflection-based search and multi-component dictionaries | Supports Codex and Pi custom proposers. |
| `autoresearch` | Long-horizon autonomous experiments | Pi uses one persistent RPC session for Ralph continuations. |
| `meta_harness` | Frontier-based proposals with outer-loop evaluation | Pi starts a fresh session per iteration and retains the workspace. |
| `best_of_n` | Bounded independent proposal sampling | No agent-runner requirement. |
| composition helpers | Multi-engine exploration and continuation | Give every stage explicit budgets. |

## Engine configuration

```python
from gepa.optimize_anything import OptimizeAnythingConfig

config = OptimizeAnythingConfig(
    engine="gepa",                    # or autoresearch, meta_harness, best_of_n
    max_evals=100,                     # evaluation-server cap
    max_token_cost=5.0,                # proposer/agent USD cap
    max_concurrency=4,
    run_dir="external-runs/example",  # engine workspace and artifacts
    output_dir="external-runs/example/output",
    sandbox=True,
    engine_config={...},
)
```

Set at least one of `max_evals` or `max_token_cost`. Use both when the run
must be bounded by evaluation work and provider spend. `run_dir` contains
engine workspaces; `output_dir` contains evaluation records and progress logs.
Keep both outside the repository when an agent engine is allowed to edit its
candidate workspace.

### `gepa`

`gepa` performs reflective mutation and Pareto-aware candidate selection. Its
`engine_config` is a `GEPAConfig`-shaped mapping. The local Codex proposer can
be attached through the nested reflection configuration:

```python
from codex_agent_proposer import CodexAgentProposer
from gepa.optimize_anything import (
    EngineConfig,
    OptimizeAnythingConfig,
    ReflectionConfig,
    optimize_anything,
)

proposer = CodexAgentProposer(
    run_dir="external-runs/example/proposer",
    model="<codex-model>",
    timeout_seconds=600,
)

result = optimize_anything(
    seed_candidate={"prompt": SEED_PROMPT},
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    objective="Improve the prompt against the evaluator.",
    config=OptimizeAnythingConfig(
        engine="gepa",
        max_evals=300,
        run_dir="external-runs/example/gepa",
        output_dir="external-runs/example/output",
        engine_config={
            "engine": EngineConfig(
                max_candidate_proposals=20,
                max_workers=1,
                parallel=False,
                cache_evaluation=True,
            ),
            "reflection": ReflectionConfig(
                reflection_lm=None,
                custom_candidate_proposer=proposer,
                module_selector="all",
            ),
        },
    ),
)
```

The proposer contract remains:

```python
(candidate, reflective_dataset, components_to_update, *, metadata=None)
    -> dict[str, str]
```

Every returned key must exactly match `components_to_update`, and every value
must be a string. See `codex.md` for the safety flags, timeout, and diagnostic
files retained by the adapter.

To use Pi for the GEPA engine, replace the proposer with
`scripts/pi_agent_proposer.py`:

```python
from pi_agent_proposer import PiAgentProposer

proposer = PiAgentProposer(
    run_dir="external-runs/example/proposer",
    pi_command="pi",
    model="provider/model",
    timeout_seconds=600,
    sandbox=True,
)
```

It uses Pi JSON mode, the read-only `read,grep,find,ls` tools, no sessions or
ambient context/extensions/skills, and preserves `pi_stdout.jsonl`,
`pi_stderr.log`, `response.json`, `usage.json`, and `error.txt` per proposal.

### `autoresearch`

This upstream engine runs a long-horizon experiment loop. The Claude-free
profile uses the maintained GEPA fork's Pi runner:

```python
OptimizeAnythingConfig(
    engine="autoresearch",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    engine_config={
        "agent_backend": "pi",
        "pi_command": "pi",
        "model": "provider/model",
        "ralph": True,
        "max_no_eval_seconds": 300,
    },
)
```

It materializes a candidate workspace and an evaluation script. Ralph sends
continuation prompts through one persistent Pi RPC process. The engine
requires authenticated `pi`, `jq`, and `curl`; Linux requires `bwrap` and
macOS requires `sandbox-exec` when `sandbox=True`. There is no implicit Claude
fallback.

### `meta_harness`

This upstream engine runs a Pi proposer while the framework owns candidate
evaluation and selection:

```python
OptimizeAnythingConfig(
    engine="meta_harness",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    engine_config={
        "agent_backend": "pi",
        "pi_command": "pi",
        "model": "provider/model",
        "max_iterations": 20,
        "max_candidates_per_iter": 3,
    },
)
```

The proposer writes candidate files; it does not run the benchmark itself.
The outer evaluation server scores each candidate and updates the frontier.
Each Pi iteration is a fresh session while the candidate/frontier workspace is
retained. The agent-engine preflight requires authenticated `pi`, `jq`, and
`curl`, plus the OS sandbox runtime when `sandbox=True`.

### `best_of_n`

`best_of_n` samples bounded independent proposals and retains the highest
scoring candidate. Configure its model and proposal count through
`engine_config` and always set an evaluation or token budget.

## Composing engines

The same task and evaluator can be reused across engines. An Omni-style run
first explores with several engines, selects the best result, and then starts
a fresh engine from that candidate:

```python
from gepa.optimize_anything import (
    OptimizeAnythingConfig,
    optimize_anything,
    optimize_best_of,
)

task = {
    "evaluator": evaluate,
    "objective": "Improve the candidate against the evaluator.",
    "background": BACKGROUND,
    "dataset": trainset,
    "valset": valset,
}

explore = optimize_best_of(
    seed,
    **task,
    configs=[
        OptimizeAnythingConfig(engine="gepa", max_evals=20),
        OptimizeAnythingConfig(
            engine="autoresearch",
            max_evals=20,
            max_token_cost=1.0,
            engine_config={
                "agent_backend": "pi",
                "model": "provider/model",
                "ralph": True,
            },
        ),
        OptimizeAnythingConfig(
            engine="meta_harness",
            max_evals=20,
            max_token_cost=1.0,
            engine_config={
                "agent_backend": "pi",
                "model": "provider/model",
            },
        ),
    ],
)

omni_result = optimize_anything(
    explore.best_candidate,
    **task,
    config=OptimizeAnythingConfig(
        engine="gepa",
        max_evals=40,
        max_token_cost=2.0,
    ),
)
```

Other available compositions are `optimize_sequential`,
`optimize_parallel`, `optimize_vote`, and `optimize_adaptive_sequential`.
Use explicit per-stage budgets and a shared external output location.

The Omni two-stage pattern is: run several engines under small budgets, select
the best candidate, then start a fresh engine with a new budget from that
candidate. This keeps stage artifacts separate and avoids pretending that an
engine's internal session can be resumed by a different engine.

## Results

The new launcher returns a `Result` with:

```python
result.best_candidate
result.best_score
result.total_evals
result.eval_log
result.metadata
```

`metadata` includes engine, budget, cost, progress-log, and output-directory
details when supplied by the upstream engine. Inspect the engine workspace and
evaluation records after every bounded run.
