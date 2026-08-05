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
text artifact. GEPA is both the Python package name and one backend; `engine="gepa"`
selects its reflective evolutionary search. The same task and evaluator can be run with
`autoresearch`, `meta_harness`, or the deliberately simple `best_of_n` baseline.

Black-box refers to the evaluator, not the candidate. The backend cannot see metric
internals or gradients; it sees the candidate, the scalar score, and the feedback in
`info`, then proposes another candidate. A rich evaluator is therefore the main source
of optimization quality.

## Candidate and evaluator model

The public launcher is string-candidate-first:

```python
result = optimize_anything(
    seed_candidate="candidate text",
    evaluator=evaluate,
    objective="Improve the candidate against the evaluator.",
    config=OptimizeAnythingConfig(engine="gepa", max_evals=20),
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

The local Codex and Pi custom proposers retain GEPA's component contract:
`proposer(candidate, reflective_dataset, components_to_update, *, metadata=None) ->
dict[str, str]`. A one-component dictionary may appear in the development
self-evaluation harness when the pinned fork's custom-proposer bridge requires named
components; that is a local compatibility extension, not the public launcher contract.

## Optimization modes

Choose a mode by the data arguments:

| mode | arguments | meaning |
| --- | --- | --- |
| Single-task | `dataset=None, valset=None` | One candidate solves one problem; call `evaluator(candidate)`. |
| Multi-task | `dataset=[...]` | One candidate is scored for each related example and selected on that dataset. |
| Generalization | `dataset=[...], valset=[...]` | Optimize on `dataset`, then select on representative unseen examples in `valset`. |

`valset` is a selection set, not the final report. Separate `valset`-based selection is
implemented by the `gepa` backend; the agentic backends fold `valset` into their scoring
pool. `test_set` is independent of these modes and reporting-only: it is sealed from the
search, scored after optimization, and should be used for an unbiased final number.

## Default Omni workflow

For a normal “optimize this” request, use the two-phase Omni workflow in
`references/omni.md`:

1. Define one shared candidate, evaluator, objective, dataset/valset, and explicit total
   budget.
2. Run `gepa`, `autoresearch`, and `meta_harness` through `optimize_best_of` in parallel.
   The local runtime uses `CodexAgentProposer` for GEPA and `agent_backend="pi"` for both
   agentic branches.
3. Select `explore.best_candidate`, then start a fresh continuation optimizer in a new
   run/output directory. Fresh GEPA is the default continuation (“Omni-GEPA”); set
   `continuation_engine` explicitly only when an AutoResearch or Meta-Harness continuation
   is wanted.
4. Pass `test_set` only to the final continuation and report its score separately from the
   selection score.

Split each supplied total budget into four balanced positive slices: three explorations
and one continuation. Omni requires an explicit usable `max_evals` and/or
`max_token_cost`; if the total cannot provide four positive slices, explain the constraint
and use a standalone engine instead. The internal
`scripts/omni_pipeline.py` helper composes the existing launcher primitives without
changing their public API.

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
config = OptimizeAnythingConfig(
    engine="gepa",
    max_evals=300,
    max_token_cost=5.0,
    stop_at_score=1.0,
    run_dir="external-runs/my-run",
    output_dir="external-runs/my-run/output",
    engine_config={
        "engine": {"max_workers": 16, "seed": 0},
        "reflection": {"reflection_minibatch_size": 5},
    },
)
```

`max_evals` caps evaluation calls; `max_token_cost` caps the backend's proposer/agent
spend. They are independent. A wall-clock timeout on the launched process is a useful
backstop. If both are `None`, the run is unbounded apart from a warning. Engine
configuration is strict: keys belong to the selected backend, and swapping
`engine=` requires swapping its `engine_config` block.

## Engine selection

- `gepa` performs in-process reflective mutation and Pareto-aware selection. The
  Codex-native local adapter is `CodexAgentProposer`; `PiAgentProposer` is also
  available for the component-level hook.
- `autoresearch` runs a long-horizon experiment loop. The maintained local profile
  uses `agent_backend="pi"` and one persistent Pi RPC session for Ralph continuations.
- `meta_harness` proposes candidates while the outer framework evaluates and selects
  them. The Pi profile starts a fresh session per iteration while the frontier workspace
  persists.
- `best_of_n` samples independent candidates and retains the best. It ignores feedback
  and history, so use it as a comparison floor.

Composition helpers—`optimize_sequential`, `optimize_parallel`, `optimize_best_of`,
`optimize_vote`, and `optimize_adaptive_sequential`—reuse the same task/evaluator. Give
each stage explicit budgets and keep stage artifacts in a shared external location. The
default Omni workflow specifically uses `optimize_best_of` for its three-way Phase 1
portfolio, then calls `optimize_anything` once for a fresh Phase 2 continuation.

## Codex-native runtime boundary

Follow `references/codex.md` for `CodexAgentProposer`. Each proposal is isolated in an
external directory, invokes `codex exec` with a read-only sandbox, and must return
structured `new_texts` whose keys exactly match `components_to_update` and whose values
are strings. Per-proposal inputs, output, usage, stderr, and errors remain available for
diagnosis.

Follow `references/pi.md` for `PiAgentProposer` and the Pi-backed agent engines. Pi's
tool allowlist is not an OS security boundary: `sandbox=True` requires `bwrap` on Linux
or `sandbox-exec` on macOS, and there is no silent unsandboxed fallback.

Run the extended local preflight before a real run:

```bash
python3 skills/gepa-omni-skill/scripts/preflight.py --engine gepa
python3 skills/gepa-omni-skill/scripts/preflight.py --engine codex
python3 skills/gepa-omni-skill/scripts/preflight.py --engine omni --agent-backend pi
```

The plugin deliberately keeps the upstream Claude-free substitutions in the local
runtime layer; it does not add a Claude CLI requirement to the Codex/Pi distribution.

## Local setup and references

The plugin ships development tooling but does not vendor GEPA. Install the maintained
engine-capable `gepa[full]` fork explicitly in the consumer environment, pinned to its
deployment URL and commit. Use `uv sync --project . --group dev` only for local tests
and linting.

- `references/api.md` — launcher contract, modes, budgets, engines, strict configuration,
  compositions, and result metadata.
- `references/omni.md` — default two-phase Omni orchestration, four-way budgets, data
  boundaries, runtime substitutions, and standalone overrides.
- `references/writing_evaluators.md` — feedback-rich evaluators, judges, batching, and
  stochastic averaging.
- `references/gotchas.md` — reward hacking, selection bias, saturated signals, stop
  conditions, and runtime prerequisites.
- `references/codex.md` — Codex proposer contract, isolation, diagnostics, and limits.
- `references/pi.md` — Pi proposer and Claude-free agent-engine behavior.
- `references/tracking.md` — optional W&B/MLflow tracking.

The semantic comparison baseline is upstream `main` at
`ba30ee24e8f63dfdb9e557ed8cfaaec7aa09a6df`. The [upstream skill](https://github.com/gepa-ai/gepa/blob/ba30ee24e8f63dfdb9e557ed8cfaaec7aa09a6df/.claude/skills/gepa-optimize-anything/SKILL.md)
is the source for shared launcher semantics; this plugin preserves the Codex packaging
and Codex/Pi runtime adapters as intentional local extensions.
