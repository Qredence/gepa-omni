# API reference — `optimize_anything`

There are two intentionally distinct API layers:

1. The published `gepa==0.1.4` package exposes the standalone reflective
   `optimize_anything()` engine.
2. This plugin exposes `run_optimization()` and `run_omni()` for the native
   AutoResearch, Meta-Harness, Best-of-N, and two-phase Omni workflows.

The native runtime is checked in under `scripts/native_omni/` and is independent
of the installed GEPA package. Its pinned MIT provenance is recorded in
[`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Mental model

`optimize_anything` is black-box optimization over a candidate. The evaluator
returns a higher-is-better score plus optional feedback; the search engine uses
that feedback to propose and select candidates. A budget bounds evaluator calls
and, for agentic plugin engines, model-token spend.

The direct PyPI API has no top-level `engine=` selector. It is the reflective
GEPA engine. Engine selection belongs to the plugin wrapper, where `gepa` means
the PyPI reflective engine and `autoresearch`, `meta_harness`, and `best_of_n`
mean plugin-native engines.

## Published PyPI GEPA API

```python
from gepa.optimize_anything import GEPAConfig, optimize_anything

result = optimize_anything(
    seed_candidate="candidate text",
    evaluator=evaluate,
    batch_evaluator=None,
    dataset=dataset,
    valset=valset,
    objective="Improve the candidate against the evaluator.",
    background="Optional context for a seedless run.",
    config=GEPAConfig(...),
)
```

`seed_candidate` may be a string, a named component mapping, or `None` when the
engine can bootstrap from `objective` and `background`. Examples in `dataset`
and `valset` are opaque values passed to the evaluator. The direct PyPI call has
no `test_set` parameter.

The published configuration is nested and strict:

```python
import os
from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig

config = GEPAConfig(
    engine=EngineConfig(
        max_metric_calls=300,
        max_workers=16,
        run_dir="/tmp/gepa-run",
    ),
    reflection=ReflectionConfig(
        reflection_lm=os.environ["OPENAI_MODEL"],
        reflection_minibatch_size=5,
    ),
)
```

Important PyPI fields:

- `EngineConfig.max_metric_calls` bounds evaluation calls.
- `EngineConfig.max_workers` and `parallel` control proposal concurrency.
- `EngineConfig.max_reflection_cost` optionally bounds reflection spend.
- `GEPAConfig.stop_callbacks` carries score or other stopping policies.
- `EngineConfig.run_dir` stores GEPA state and diagnostics. PyPI 0.1.4 does
  not take a direct `output_dir` argument.

Unknown or misspelled fields raise `TypeError`; do not pass the plugin wrapper's
`max_evals`, `max_token_cost`, `engine_config`, or `output_dir` fields to this
direct API.

## Evaluator and data splits

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    score = grade(output, example)
    return score, {
        "output": output,
        "expected": example.get("gold"),
        "error": example.get("error"),
    }
```

Use `evaluator(candidate)` for a single task and
`evaluator(candidate, example)` with `dataset` or `valset`. A bare float is
accepted, but `(score, info)` gives the proposer useful failure details.

`batch_evaluator` accepts a list of `(candidate, example)` pairs and must return
one score or `(score, info)` result per pair in the same order. The plugin-native
evaluation server applies the same normalization and enforces batch cardinality.

| mode | configuration | selection behavior |
| --- | --- | --- |
| Single-task | `dataset=None, valset=None` | Solve one hard problem. |
| Multi-task | `dataset=[...]` | Score and select on the shared dataset. |
| Generalization | `dataset=[...], valset=[...]` | Optimize on `dataset`, select on `valset`. |

For the plugin wrapper, put held-out examples in `task["test_set"]`. They are
never exposed through the native agent task endpoint or passed to Phase 1; the
wrapper scores them after optimization and may expose `metadata["test_score"]`
and `metadata["test_scores"]`.

## Plugin wrapper

```python
from omni_pipeline import run_optimization

result = run_optimization(
    "candidate text",
    task={
        "evaluator": evaluate,
        "dataset": trainset,
        "valset": valset,
        "test_set": heldout,
        "objective": "Improve the candidate.",
    },
    engine="autoresearch",
    max_evals=100,
    max_token_cost=5.0,
    run_dir="/tmp/gepa-native-run",
    output_dir="/tmp/gepa-native-output",
    agent_backend="codex",
)
```

`engine="gepa"` routes to PyPI `gepa==0.1.4` and requires its nested
`GEPAConfig` contract. The other explicit engines are plugin-native and use a
shared `Task`, `BudgetTracker`, and external evaluation workspace. Omitting
`engine` selects `run_omni()`.

All wrapper `run_dir` and `output_dir` paths must be absolute and outside the
development checkout. Native results expose `best_candidate`, `best_score`,
`total_evals`, `eval_log`, and serializable `metadata`; `Result.persist()` writes
`result.json` to the external output directory.

## Engine behavior

| engine | implementation | behavior |
| --- | --- | --- |
| `gepa` | Published PyPI GEPA plus `CodexAgentProposer` over Chat Completions | Reflect on feedback, mutate candidates, and keep a Pareto-aware frontier. |
| `autoresearch` | Plugin-native `native_omni` runtime | Long-horizon experiment loop with an optional persistent agent session. |
| `meta_harness` | Plugin-native `native_omni` runtime | Fresh agent proposals against a persistent frontier/workspace. |
| `best_of_n` | Plugin-native `native_omni` runtime | Independent candidate samples; keep the highest-scoring candidate. |

The native engines use the same evaluator and budget ledger. `BudgetTracker`
reserves capacity before concurrent evaluation and raises `BudgetExhausted`
instead of allowing an over-budget call. Train and validation data are served
over a loopback-only HTTP API; the held-out split is deliberately unavailable.

## Omni orchestration

The default Omni workflow runs `gepa`, `autoresearch`, and `meta_harness` in
parallel, chooses the best Phase 1 candidate, and runs a fresh Phase 2
continuation. `test_set` is withheld from all Phase 1 branches. The default
continuation is `gepa`; set `continuation_engine` to `autoresearch` or
`meta_harness` to choose a native continuation.

```python
from omni_pipeline import run_omni

result = run_omni(
    "candidate text",
    task=task,
    max_evals=40,
    max_token_cost=20.0,
    run_dir="/tmp/omni-run",
    output_dir="/tmp/omni-output",
    continuation_engine="gepa",
    agent_backend="codex",
    codex_input_cost_per_million=2.0,
    codex_output_cost_per_million=8.0,
)
```

The total budget is partitioned into three exploration slices and one
continuation slice. `max_evals` must be at least four when it is the only bound;
an explicit positive `max_evals` and/or `max_token_cost` is required.

## Backend parameters and sandbox contract

The wrapper preserves these backend parameters:

- `agent_backend`: `codex` (default), `pi`, or `claude` for native agent loops.
- `agent_model`, `codex_model`, `pi_model`, and the backend command parameters:
  retained compatibility fields; `OPENAI_MODEL` and the shared API environment
  are authoritative.
- `codex_timeout_seconds`: bounded Chat Completions request timeout.
- `codex_input_cost_per_million` and `codex_output_cost_per_million`: required
  together when a Chat Completions `max_token_cost` cap is configured.
- `max_concurrency`, `gepa_parallel_proposals`, `stop_at_score`, and
  `continuation_engine` for orchestration and budget control.

Set `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` for every model
call. Backend labels are preserved in metadata, but do not select a CLI or a
different provider.

When the plugin is invoked through the interactive skill, it asks only for a
missing `OPENAI_MODEL` or `OPENAI_BASE_URL` and applies the answers to the
current process. It never persists prompted values. `OPENAI_API_KEY` must be
configured through the environment or a secret manager and is never requested
in chat. Preflight remains non-interactive and validates the final environment
before launch.

Every wrapper run requires `sandbox=True`; `sandbox=False` is rejected. The
model receives request text and JSON but no local tools. Run and output paths
remain external to the checkout.

## Codex proposer boundary

`CodexAgentProposer` is the read-only Chat Completions adapter used by the PyPI
`gepa` engine. Each proposal gets a unique external directory and sends the
materialized context to `/chat/completions`. Its structured result must contain
`new_texts` whose keys exactly match `components_to_update` and whose values are
strings. Inputs, output, usage, and errors remain in the proposal directory.

Native `OpenAIChatCompletionRunner` retains session message history for
AutoResearch, records API usage/cost, and writes raw responses to an external
native engine workspace. The old CLI runner classes remain compatibility
exports but are not constructed by the plugin pipeline.

## Pi and Claude runners

`PiAgentProposer` and the `pi`/`claude` backend labels use the same Chat
Completions client. They retain compatibility names and backend metadata; no
provider-specific executable or local login is required.

## Preflight

Run preflight before a long or live run:

```bash
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine gepa
uv run python skills/gepa-omni-skill/scripts/preflight.py \
  --engine autoresearch --agent-backend codex
uv run python skills/gepa-omni-skill/scripts/preflight.py \
  --engine omni --agent-backend pi
```

Preflight checks the installed PyPI GEPA API for `gepa`/Omni, imports the
plugin-native runtime for native engines, and verifies
`OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY`. It does not call a
model unless `--test-lm` is supplied.

## Results and tracking

The PyPI result exposes GEPA's native result fields, including the best candidate,
validation scores, and run directory. The plugin-native `Result` is JSON
serializable and persists evaluation/trace files under the external output
directory. Optional W&B/MLflow tracking for PyPI GEPA is documented in
[`tracking.md`](tracking.md).
