---
name: gepa-omni-skill
description: >-
  Automatically improve any scorable text artifact—prompts, programs, configs, specs,
  regex/SQL/schemas, agent scaffolds, or encoded search solutions—with the
  engine-pluggable optimize_anything API. Use when tuning a candidate from objective
  metrics, execution feedback, or LLM-as-judge scores, when designing an evaluator, or
  when running the default Omni portfolio across GEPA, AutoResearch, and Meta-Harness.
---

# `optimize_anything`

Use `gepa.optimize_anything.optimize_anything` to perform black-box optimization over a
text artifact. The published `gepa==0.1.4` package is the standalone reflective
engine. This plugin adds native `autoresearch`, `meta_harness`, and `best_of_n`
engines plus the default two-phase Omni orchestration.

Dependency boundary: install `gepa[full]==0.1.4` for the standalone reflective
engine and its Codex proposer. The native agent engines use the checked-in
`scripts/native_omni/` runtime and do not require a separate GEPA checkout.

Black-box refers to the evaluator, not the candidate. The backend cannot see metric
internals or gradients; it sees the candidate, the scalar score, and the feedback in
`info`, then proposes another candidate. A rich evaluator is therefore the main source
of optimization quality.

## Candidate and evaluator model

The public launcher is string-candidate-first:

```python
import os
from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig

result = optimize_anything(
    seed_candidate="candidate text",
    evaluator=evaluate,
    objective="Improve the candidate against the evaluator.",
    config=GEPAConfig(
        engine=EngineConfig(max_metric_calls=20),
        reflection=ReflectionConfig(reflection_lm=os.environ["OPENAI_MODEL"]),
    ),
)
```

The candidate can be any string an evaluator can score: a prompt, program, config,
schema, SQL query, regular expression, agent instruction, plan, or other encoded search
artifact. Use `None` only when the selected engine can bootstrap a candidate from the
objective and background.

An evaluator returns a higher-is-better score, optionally with actionable feedback:

```python
def evaluate(candidate: str, example) -> tuple[float, dict]:
    output = run_system(candidate, example)
    score = grade(output, example)
    return score, {"output": output, "expected": example.get("gold")}
```

Return failures, outputs, diffs, and partial-credit signals in `info`; `{"score": 0}`
alone gives the proposer no useful direction. For stochastic systems, average multiple
samples inside the evaluator instead of selecting on a lucky single sample. See
`references/writing_evaluators.md`.

The local Codex/Pi custom proposers retain GEPA's component contract:
`proposer(candidate, reflective_dataset, components_to_update, *, metadata=None) ->
dict[str, str]`. A one-component dictionary may appear in the development
self-evaluation harness when a compatibility bridge requires named components;
that is a local adapter boundary, not the direct PyPI launcher contract.

## Optimization modes

Choose a mode by the data arguments:

| mode | arguments | meaning |
| --- | --- | --- |
| Single-task | `dataset=None, valset=None` | One candidate solves one problem; call `evaluator(candidate)`. |
| Multi-task | `dataset=[...]` | One candidate is scored for each related example and selected on that dataset. |
| Generalization | `dataset=[...], valset=[...]` | Optimize on `dataset`, then select on representative unseen examples in `valset`. |

`valset` is a selection set, not the final report. The direct PyPI
`optimize_anything()` call has no `test_set` parameter. The plugin wrapper
`run_optimization(..., engine="gepa")` may receive `task["test_set"]`, score it
after the PyPI run, and attach that held-out score to its wrapper result.

## Default Omni workflow

For a normal “optimize this” request, use the two-phase plugin-native Omni workflow in
`references/omni.md`:

1. Define one shared candidate, evaluator, objective, dataset/valset, and explicit total
   budget.
2. Run the three isolated Phase 1 branches through the plugin-native coordinator:
   PyPI reflective `gepa`, native `autoresearch`, and native `meta_harness`. The
   selected `agent_backend` drives the two writable branches; Codex is the default.
3. Select `explore.best_candidate`, then start a fresh continuation optimizer in a new
   run/output directory. Fresh GEPA is the default continuation (“Omni-GEPA”); set
   `continuation_engine` explicitly only when an AutoResearch or Meta-Harness continuation
   is wanted.
4. Pass `test_set` only to the final continuation and report its score
   separately from the selection score.

Split each supplied total budget into four balanced positive slices: three explorations
and one continuation. Omni requires an explicit usable `max_evals` and/or
`max_token_cost`; if the total cannot provide four positive slices, explain the constraint
   and use a standalone engine instead. The internal
   `scripts/omni_pipeline.py` helper composes the native runtime and the explicit
   PyPI GEPA adapter without changing the public launcher API.

### Backend-specific model selection

All engines use the same OpenAI-compatible Chat Completions model. Configure
the endpoint once so the GEPA proposer, AutoResearch, Meta-Harness, and fresh
continuation share it:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

`agent_backend` remains a compatibility label (`codex`, `pi`, or `claude`), while
`OPENAI_MODEL` and the three `OPENAI_*` variables are authoritative. Legacy
model and command parameters remain accepted for source compatibility.

Set `gepa_parallel_proposals=(parents, mutations)` to opt GEPA into P×N sampling
with `AllImprovements`; otherwise Omni keeps its sequential one-worker GEPA
configuration. Set `max_concurrency` high enough to support the requested parallel
proposals.

## Workflow

1. Define the candidate, the real goal, the score ceiling if one exists, and the
   evaluator feedback needed to improve a failure.
2. Pick single-task, multi-task, or generalization mode. Make `valset` representative;
   keep `test_set` untouched until final reporting.
3. Use Omni by default. For comparison or debugging, explicitly select one standalone
   engine: `gepa`, `autoresearch`, `meta_harness`, or `best_of_n`; an explicit engine
   bypasses Omni orchestration.
4. Size the budget for many proposal rounds: roughly 15–20 times the size of the
   selection set (`len(valset)`, `len(dataset)`, or 15–20 for single-task). Set an
   explicit total `max_evals` and/or `max_token_cost`; Omni divides each into four
   slices. Add `stop_at_score` when the metric has a known ceiling.
5. Keep `run_dir` and `output_dir` outside the checkout when an engine or evaluator
   needs a workspace. Run preflight before a long run.
6. Launch, inspect the first evaluation chain in each Phase 1 branch, and then review
   `result.best_candidate`, `best_score`, `total_evals`, `eval_log`, `metadata`, and the
   external run artifacts for both phases.
7. Report the held-out `test_set` score separately from the selection score.

Example configuration:

```python
import os

config = GEPAConfig(
    engine=EngineConfig(
        seed=0,
        max_metric_calls=300,
        max_workers=16,
        run_dir="external-runs/my-run",
    ),
    reflection=ReflectionConfig(
        reflection_lm=os.environ["OPENAI_MODEL"],
        reflection_minibatch_size=5,
    ),
)
```

For direct PyPI GEPA, `EngineConfig.max_metric_calls` caps evaluation calls;
`EngineConfig.max_reflection_cost` caps reflection spend, and
`GEPAConfig.stop_callbacks` carries stopping policy. These are nested typed
configuration fields—not top-level `max_evals`, `max_token_cost`, `engine=`,
`engine_config`, or `output_dir` arguments. The plugin-native Omni wrapper has
its own explicit `max_evals` and `max_token_cost` budgets.

## Engine selection

- `gepa` performs in-process reflective mutation and Pareto-aware selection through
  the pinned PyPI package and the external read-only Codex proposer.
- `autoresearch` runs a plugin-native long-horizon experiment loop. The local
  profile defaults to `agent_backend="codex"` and carries one external
  workspace/session lineage across Ralph continuations; `pi` and `claude`
  remain explicit alternatives.
- `meta_harness` is plugin-native: it proposes candidates while the outer framework evaluates and selects
  them. The default Codex profile starts a fresh ephemeral session per iteration
  while the frontier workspace persists; `pi` and `claude` remain explicit alternatives.
- `best_of_n` is plugin-native, samples independent candidates, and retains the best. It ignores feedback
  and history, so use it as a comparison floor.

Composition helpers—`optimize_sequential`, `optimize_parallel`, `optimize_best_of`,
`optimize_vote`, and `optimize_adaptive_sequential`—remain part of the direct PyPI
launcher when available. Give each direct-PyPI stage explicit budgets and keep stage
artifacts in a shared external location. The default Omni workflow uses the shipped
native coordinator for its three-way Phase 1 portfolio, then starts one fresh Phase 2
continuation.

## Chat Completions runtime boundary

Follow `references/codex.md` for `CodexAgentProposer`. Each proposal is isolated in an
external directory, sends its materialized context through Chat Completions, and must
return structured `new_texts` whose keys exactly match `components_to_update` and whose
values are strings. Per-proposal inputs, output, usage, and errors remain available for
diagnosis.

The native `OpenAIChatCompletionRunner` sends prompt text and JSON, retains
AutoResearch message history, and writes raw responses to an external workspace.
The model does not receive local shell or filesystem tools. `sandbox=False` is
rejected at the wrapper boundary. When `max_token_cost` is set, provide both
explicit input and output USD-per-million-token rates.

Follow `references/pi.md` for `PiAgentProposer` and the explicit Pi-backed agent
compatibility label. It uses the same Chat Completions endpoint; no Pi CLI or
provider-specific login is required.

Run the extended local preflight before a real run. It checks the pinned GEPA
surface where relevant, the native runtime, and the three `OPENAI_*` variables:

```bash
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine gepa
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine codex
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine omni
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine omni --agent-backend pi
```

The plugin keeps backend selection as runtime metadata: `codex` is the default
label, while Pi and Claude are explicit alternatives. All labels use the same
Chat Completions model configuration.

## Local setup and references

The plugin ships development tooling but does not vendor GEPA. The development
environment installs the published `gepa[full]==0.1.4` API via
`uv sync --project . --group dev`. Native Omni engine code is shipped in the
plugin under `scripts/native_omni/`; it is tested and staged with the skill.

- `references/api.md` — launcher contract, modes, budgets, engines, strict configuration,
  compositions, and result metadata.
- `references/omni.md` — default two-phase Omni orchestration, four-way budgets, data
  boundaries, runtime substitutions, and standalone overrides.
- `references/writing_evaluators.md` — feedback-rich evaluators, judges, batching, and
  stochastic averaging.
- `references/gotchas.md` — reward hacking, selection bias, saturated signals, stop
  conditions, and runtime prerequisites.
- `references/codex.md` — Codex proposer contract, isolation, diagnostics, and limits.
- `references/pi.md` — Pi proposer and explicit Pi agent-engine behavior.
- `references/tracking.md` — optional W&B/MLflow tracking.
- `THIRD_PARTY_NOTICES.md` — pinned MIT provenance for native runtime portions.

The native runtime adaptation is pinned to the MIT-licensed GEPA commit
[`8a2bed96385202f69caaeb5327a843ed2f5ea225`](https://github.com/gepa-ai/gepa/tree/8a2bed96385202f69caaeb5327a843ed2f5ea225);
see `THIRD_PARTY_NOTICES.md` for attribution and license scope. The published
PyPI 0.1.4 API remains the reference for standalone reflective launcher
semantics; native Codex/Pi runtime behavior is an intentional plugin extension.
