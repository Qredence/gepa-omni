# API reference — `optimize_anything`

The public entry point is `gepa.optimize_anything.optimize_anything`. This
reference follows upstream `main` at
`ba30ee24e8f63dfdb9e557ed8cfaaec7aa09a6df` while documenting the Codex/Pi
adapters supplied by this plugin.

## Mental model

`optimize_anything` is a general black-box optimization tool. GEPA is one
specific backend (`engine="gepa"`) and also the name of the Python package
that ships the launcher and other engines.

Four pieces make up a run:

1. **Candidate** — the text artifact being optimized.
2. **Evaluator** — a callable that scores a candidate and returns optional
   feedback. The backend never sees metric internals or gradients; it only
   sees the candidate, score, and `info` you return.
3. **Engine** — the search backend that proposes candidates and chooses among
   evaluated candidates.
4. **Budget** — evaluation calls and/or proposer-model spend that bound the
   run.

The evaluator is the quality bottleneck. Return concrete failures, outputs,
diffs, and partial-credit signals in `info`, not only a scalar.

## Public signature

```python
from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything

result = optimize_anything(
    seed_candidate: str | None = None,
    *,
    evaluator=None,
    batch_evaluator=None,
    dataset=None,
    valset=None,
    objective=None,
    background=None,
    test_set=None,
    config=OptimizeAnythingConfig(...),
)
```

`seed_candidate` is a **single string** at this high-level API. `None` is a
seedless run when the selected engine can bootstrap from `objective` and
`background`. Examples in `dataset`, `valset`, and `test_set` are opaque
objects understood by the evaluator.

The lower-level
`gepa.gepa_launcher.optimize_anything` API can represent named multi-component
dictionary candidates. The local development self-evaluation harness may also
pass a one-component mapping to the pinned fork when its custom-proposer
bridge requires named components. That is an explicitly scoped local
compatibility extension, not a change to the public string-candidate launcher
contract. The Codex/Pi proposer itself keeps the separate component contract:

```python
proposer(candidate, reflective_dataset, components_to_update, *, metadata=None)
    -> dict[str, str]
```

## Evaluator contract

In single-task mode, use `evaluator(candidate)`. With a `dataset` or `valset`,
use `evaluator(candidate, example)`:

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    score = grade(output, example)  # higher is better
    return score, {
        "output": output,
        "expected": example.get("gold"),
        "error": example.get("error"),
    }
```

A bare float is accepted by the launcher, but it gives the proposer no
feedback. Prefer `(score, info)` and make `info` actionable. When evaluation
is more efficient in batches, `batch_evaluator` accepts a list of
`(candidate, example)` pairs and returns one score or score/info result per
pair in the same order.

## Optimization modes and data splits

The mode is implicit in the data arguments:

| mode | configuration | evaluator call | selection behavior |
| --- | --- | --- | --- |
| Single-task | `dataset=None, valset=None` | `evaluator(candidate)` | Solve one hard problem. |
| Multi-task | `dataset=[...]`, `valset=None` | `evaluator(candidate, example)` | Score and select on the shared dataset. |
| Generalization | `dataset=[...]`, `valset=[...]` | `evaluator(candidate, example)` | Optimize on `dataset`, select on `valset`. |

The `gepa` backend is designed around separate `valset`-based selection. The
agentic backends fold `valset` into their training/scoring pool; they can still
generalize, but do not provide the same separate selection split.

`test_set` is separate from the modes and is reporting-only. It is not shown
to the optimizer, does not affect selection or budgets, and is evaluated after
the run for an unbiased comparison. When provided, inspect
`result.metadata["test_score"]`, `result.metadata["test_scores"]`,
`result.metadata["baseline_test_score"]`, and
`result.metadata["baseline_test_scores"]`.

## Configuration and budgets

```python
config = OptimizeAnythingConfig(
    engine="gepa",                    # or autoresearch, meta_harness, best_of_n
    max_evals=300,                    # evaluation-server cap
    max_token_cost=5.0,               # proposer/agent USD cap
    stop_at_score=1.0,                # use when the metric has a known ceiling
    max_concurrency=16,
    run_dir="external-runs/example",
    output_dir="external-runs/example/output",
    sandbox=True,
    engine_config={...},
)
```

- `max_evals` bounds calls to the evaluation server.
- `max_token_cost` bounds the optimizer's own reflection or agent-model spend.
- `stop_at_score` stops as soon as a candidate reaches a known optimum.
- A wall-clock timeout on the launched process is a useful additional
  backstop; it is distinct from a per-proposer timeout.
- If both `max_evals` and `max_token_cost` are `None`, the run is unbounded
  apart from a warning.
- `run_dir` stores engine workspaces and state. `output_dir` stores evaluation
  records and progress logs. Keep both outside the repository for agent runs.

### Size `max_evals` for proposal rounds

Every proposed candidate is evaluated on the full selection set by the `gepa`
backend and the `best_of_n` baseline. A practical floor is roughly 15–20
proposal candidates:

```text
generalization: max_evals ≳ 15–20 × len(valset)
multi-task:      max_evals ≳ 15–20 × len(dataset)
single-task:     max_evals ≳ 15–20
```

The agentic engines decide how to spend their calls, so treat these as a floor
rather than an exact formula for them. If the run makes only one proposal,
the budget was too low to be meaningful. Use `max_token_cost` as a second
bound, especially for agentic engines. If evaluation caching is enabled,
`max_evals` counts cache misses, so `stop_at_score` and/or
`max_token_cost` become especially important.

`engine_config` is strict and backend-specific. Unknown or misspelled keys
raise `TypeError` at construction. Swapping `engine=` requires replacing the
entire `engine_config` block with keys for that engine.

## Engines

| engine | proposal strategy | runtime |
| --- | --- | --- |
| `gepa` | Reflect on evaluator feedback, mutate candidates, and keep a Pareto frontier. | In-process; use a reflection LM or a custom proposer. |
| `autoresearch` | Run a long-horizon experiment loop in a candidate workspace. | Claude upstream; Pi in the local Claude-free profile. |
| `meta_harness` | Propose candidates from frontier/history while the framework evaluates them. | Claude upstream; Pi in the local Claude-free profile. |
| `best_of_n` | Independently sample candidates and keep the best. | Baseline; no feedback or history. |

Composition helpers include `optimize_sequential`, `optimize_parallel`,
`optimize_best_of`, `optimize_vote`, and `optimize_adaptive_sequential`. Reuse
the same evaluator and pass explicit per-stage budgets. `optimize_vote`
re-scores branch winners once outside the branch budgets, which is useful when
engines have different scoring quirks.

## Omni default orchestration

The plugin's default “optimize this” behavior is a two-phase composition built
from the existing launcher primitives. It is not a new public `engine="omni"`
backend and it does not classify tasks:

```python
explore = optimize_best_of(
    seed_candidate,
    evaluator=evaluator,
    dataset=dataset,
    valset=valset,
    objective=objective,
    configs=[
        gepa_config,             # CodexAgentProposer locally
        autoresearch_pi_config,  # agent_backend="pi"
        meta_harness_pi_config,  # agent_backend="pi"
    ],
)

result = optimize_anything(
    explore.best_candidate,
    evaluator=evaluator,
    dataset=dataset,
    valset=valset,
    objective=objective,
    test_set=test_set,
    config=fresh_gepa_config,
)
```

All three Phase 1 configs share the same evaluator, objective, dataset, and
selection `valset`. `test_set` is omitted from Phase 1 and is supplied only to
the fresh Phase 2 continuation for final reporting. `optimize_best_of` selects
the highest-scoring branch winner; Phase 2 starts a new optimizer with new
`run_dir` and `output_dir` paths rather than resuming a branch.

For Omni, split each supplied total budget independently into four balanced
positive slices: GEPA, AutoResearch, Meta-Harness, and the continuation. An
integer evaluation remainder goes to the continuation. At least four positive
evaluations are therefore required when `max_evals` is the only bound, and an
explicit positive `max_evals` and/or `max_token_cost` is required in every
case. If the total budget cannot support four slices, use an explicit
standalone engine instead.

The default continuation is fresh `engine="gepa"` with the Codex proposer
(“Omni-GEPA”). `continuation_engine="autoresearch"` and
`continuation_engine="meta_harness"` are explicit alternatives using the
local Pi backend. An explicit standalone `engine="gepa"`,
`"autoresearch"`, `"meta_harness"`, or `"best_of_n"` bypasses this
orchestration and receives the full supplied budget. The local implementation
is the internal `scripts/omni_pipeline.py` helper; the public launcher remains
string-candidate-first and its proposer bridge remains component-based only
inside the Codex/Pi adapter boundary.

## `gepa` backend and Codex proposer

The `gepa` backend accepts a `GEPAConfig`-shaped `engine_config` mapping. The
local Codex proposer is attached through the reflection configuration:

```python
from codex_agent_proposer import CodexAgentProposer
from gepa.optimize_anything import (
    EngineConfig,
    OptimizeAnythingConfig,
    ReflectionConfig,
    optimize_anything,
)

SEED_PROMPT = "You are an expert. Solve the task. Output only the answer."
proposer = CodexAgentProposer(
    run_dir="external-runs/example/proposer",
    model="<codex-model>",
    timeout_seconds=600,
)

# The public seed is a string. The pinned fork's custom-proposer bridge may
# internally expose named components to the proposer; see codex.md for that
# local compatibility boundary.
result = optimize_anything(
    seed_candidate=SEED_PROMPT,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    test_set=testset,
    objective="Improve the prompt against the evaluator.",
    config=OptimizeAnythingConfig(
        engine="gepa",
        max_evals=300,
        stop_at_score=1.0,
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

The adapter's proposer contract is intentionally component-based. Every
returned key must exactly match `components_to_update`; every returned value
must be a string. See `codex.md` for the read-only flags, timeout, and
diagnostic files.

## Pi-backed agent engines

The local Claude-free profile uses the maintained fork's generic Pi runner:

```python
OptimizeAnythingConfig(
    engine="autoresearch",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    run_dir="external-runs/autoresearch",
    output_dir="external-runs/autoresearch/output",
    engine_config={
        "agent_backend": "pi",
        "pi_command": "pi",
        "model": "provider/model",
        "ralph": True,
        "max_no_eval_seconds": 300,
    },
)
```

AutoResearch keeps one persistent Pi RPC process for Ralph continuations.
Meta-Harness starts a fresh Pi session each iteration while retaining the
frontier and candidate workspace. Pi requires `jq` and `curl` for
AutoResearch, plus `bwrap` on Linux or `sandbox-exec` on macOS when
`sandbox=True`. There is no implicit Claude fallback. See `pi.md`.

The separate `PiAgentProposer` uses Pi JSON mode with no session, ambient
context files, extensions, skills, or write tools. It implements the same
component proposer contract as `CodexAgentProposer`.

## Other backend configuration

`autoresearch` accepts `model`, `ralph`, `max_no_eval_seconds`, `handoffs`,
and agent-specific effort/thinking settings. `meta_harness` accepts `model`,
`max_iterations`, `max_candidates_per_iter`, and its agent-specific settings.
`best_of_n` accepts `model`, `temperature`, `max_n`, `lm_kwargs`, and optional
effort/thinking settings. Use only keys supported by the selected backend.

## Composing engines

The task and evaluator can be reused across stages:

```python
from gepa.optimize_anything import OptimizeAnythingConfig, optimize_sequential

result = optimize_sequential(
    SEED_PROMPT,
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    objective="Improve the candidate.",
    configs=[
        OptimizeAnythingConfig(
            engine="best_of_n",
            max_evals=20,
            max_token_cost=1.0,
        ),
        OptimizeAnythingConfig(
            engine="gepa",
            max_evals=300,
            stop_at_score=1.0,
            max_token_cost=2.0,
        ),
    ],
)
```

The sequential helper feeds each stage's best candidate into the next. The
parallel helpers return results in config order; `optimize_best_of` chooses the
highest reported score, while `optimize_vote` re-scores branch winners for a
fair cross-engine choice. `optimize_adaptive_sequential` rotates engines on
plateaus under a shared evaluation pool. Keep each stage's artifacts external
and make every budget explicit.

## Results

The launcher returns a `Result` with:

```python
result.best_candidate  # str
result.best_score      # float on the selection set
result.total_evals     # int
result.eval_log        # list[dict]
result.metadata        # engine, budgets, costs, and output paths when supplied
```

The GEPA metadata can include the full `gepa_result`; all engines may expose
budget, cost, wall-time, `output_dir`, and progress-log details. When `test_set`
is supplied, keep its `test_score` separate from `best_score`, which is the
selection-set result.

## Preflight

Use the plugin's extended preflight before a real run:

```bash
python3 skills/gepa-omni-skill/scripts/preflight.py --engine gepa
python3 skills/gepa-omni-skill/scripts/preflight.py --engine codex
python3 skills/gepa-omni-skill/scripts/preflight.py --engine omni --agent-backend pi
```

It checks the current launcher, engine registry, composition helpers, the
selected Codex/Pi proposer surface, credentials, and OS sandbox prerequisites.
It does not make model calls unless `--test-lm` is explicitly requested.
