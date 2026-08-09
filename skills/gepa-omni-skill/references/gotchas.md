# Gotchas and pitfalls

Read before any real run. Every backend optimizes exactly the score and
feedback returned by your evaluator.

## 1. Reward hacking

A weak proxy gets gamed. For example, a correctness-only score can reward a
code candidate that wraps a reference implementation while doing none of the
work that matters. Gate the score on validity and correctness, then increase
it only for the real objective. Sanity-check the winning candidate against the
actual goal, not only the reported score.

## 2. Selection bias and winner's curse

In generalization mode, the selected candidate is the maximum among candidates
scored on `valset`. A small or noisy validation set makes that maximum
optimistic. Use a representative `valset` and average N samples inside the
evaluator when the system is stochastic. `test_set` does not reduce selection
bias; it is reporting-only, but it gives an honest held-out number to report.

## 3. Stochastic evaluation defaults to N=1

The evaluator is called once per candidate/example pair by default. For a
temperature-bearing model, that is a single-sample estimate. Average multiple
samples inside `evaluate` and return the sample details in `info`; budget for
the extra calls.

## 4. Candidate shape depends on the boundary

Direct PyPI `optimize_anything()` accepts a string, a named component mapping,
or `None`. The plugin wrapper `run_optimization()` accepts a string seed;
custom proposers return `dict[str, str]` at their separate component boundary.

## 5. The default budget may be too small

For direct PyPI GEPA, `EngineConfig.max_metric_calls` is a cap on evaluation calls, not a count
of meaningful proposal rounds. For the `gepa` backend, size it roughly as:

```text
generalization: max_metric_calls ≳ 15–20 × len(valset)
multi-task:      max_metric_calls ≳ 15–20 × len(dataset)
single-task:     max_metric_calls ≳ 15–20
```

Every candidate is scored on the full selection set. If a run stops after one
proposal, increase the budget. PyPI config uses
`EngineConfig.max_reflection_cost` for reflection spend; the plugin-native Omni
wrapper separately accepts `max_token_cost`.

## 6. Give every run a real stop condition

Use `GEPAConfig.stop_callbacks` whenever the metric has a known ceiling such
as accuracy or pass rate. If evaluation caching is enabled,
`max_metric_calls` counts cache misses, so a converged run can continue
proposing without consuming evaluation budget; a score stop, token cap, and
process timeout then become essential.

## 7. PyPI configuration is nested and strict

PyPI 0.1.4 uses `GEPAConfig(engine=EngineConfig(...),
reflection=ReflectionConfig(...))`. An unknown, misspelled, or stale key
raises `TypeError` at construction. It does not accept top-level `engine=`,
`engine_config`, `max_evals`, `max_token_cost`, or `output_dir`.

## 8. Saturated signals return the seed

The GEPA backend learns from examples the seed gets wrong. If the seed already
scores at the ceiling on the selected examples, proposals may be rejected and
the seed returned unchanged. This is not necessarily a failed run: add hard
examples with real failure feedback, inspect accepted proposals, or compare
against `best_of_n`, which does not use the same reflective acceptance gate.

## 9. Evaluator exceptions abort by default

`EngineConfig.raise_on_exception` defaults to `True`. Catch expected failures
and return a low score with `info["error"]` or detailed `error_*` fields so the
proposer can learn from them. If appropriate, configure
`raise_on_exception=False` to convert exceptions to score `0.0`; do not hide
unexpected failures behind a success-shaped result.

## 10. Chat Completions configuration is a hard requirement

- Every model call uses the OpenAI-compatible Chat Completions endpoint from
  `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY`.
- `agent_backend="codex"`, `"pi"`, or `"claude"` is retained as a runtime
  compatibility label; it does not invoke a provider CLI.
- When `max_token_cost` is set, both input and output USD-per-million token
  rates are required and validated before launch. Without a USD cap, usage-only
  runs are allowed but cost is marked unknown when rates are absent.
- The model receives prompt text and materialized JSON, not local shell or
  filesystem tools. Keep `run_dir` and `output_dir` outside the checkout for
  diagnostics and evaluator workspaces.
- `sandbox=False` remains a hard wrapper failure so callers cannot accidentally
  disable the runtime safety boundary.
- Do not conflate the read-only `CodexAgentProposer` used by PyPI `gepa` with
  the native `OpenAIChatCompletionRunner` used by AutoResearch and Meta-Harness;
  both use the same API boundary but have different lifecycle contracts.
- Keep `run_dir` and `output_dir` outside the checkout when agents need a
  workspace or persistent diagnostics.

## Quick preflight checklist

- [ ] The string-candidate API and selected optimization mode match the goal.
- [ ] The evaluator returns a higher-is-better score and actionable feedback.
- [ ] Direct PyPI calls use only `dataset` and `valset`; wrapper-level
      `task["test_set"]` is held-out post-run reporting.
- [ ] The seed has failures the selected backend can learn from.
- [ ] `max_metric_calls` is sized for many proposal rounds.
- [ ] PyPI stopping uses `stop_callbacks`; plugin-native Omni uses its explicit
      budget controls.
- [ ] A PyPI `GEPAConfig` uses only its nested typed fields.
- [ ] The selected backend and proposer model have been tested with one call
      where live credentials are available.
- [ ] Pi/Codex credentials and OS sandbox prerequisites are present for the
      selected runtime.
- [ ] External run and output directories are configured when artifacts matter.
- [ ] A separate held-out score is reported when unbiased measurement matters.
