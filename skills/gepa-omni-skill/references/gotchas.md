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

## 4. Candidate shape is string-first

The high-level `optimize_anything` launcher accepts a single string candidate.
Do not pass a dictionary when comparing engines or composing pipelines. Named
dictionary components belong to the lower-level GEPA launcher. This plugin's
Codex/Pi custom proposer still returns `dict[str, str]`, and its development
self-evaluation harness may use a one-component mapping only when the pinned
fork's custom-proposer bridge requires it.

## 5. The default budget may be too small

`max_evals` defaults to 100, but it is a cap on evaluation calls, not a count
of meaningful proposal rounds. For the `gepa` backend, size it roughly as:

```text
generalization: 15–20 × len(valset)
multi-task:      15–20 × len(dataset)
single-task:     15–20
```

Every candidate is scored on the full selection set. If a run stops after one
proposal, increase the budget. `max_token_cost` separately limits proposer or
agent-model spend, and a wall-clock timeout should be used as a process
backstop for long agent runs.

## 6. Give every run a real stop condition

Use `stop_at_score` whenever the metric has a known ceiling such as accuracy
or pass rate. Use `max_token_cost` for agentic engines. If evaluation caching
is enabled, `max_evals` counts cache misses, so a converged run can continue
proposing without consuming evaluation budget; a score stop, token cap, and
process timeout then become essential. If both `max_evals` and
`max_token_cost` are `None`, the run is unbounded apart from a warning.

## 7. `engine_config` is strict

Each backend parses `engine_config` into its own typed configuration. An
unknown, misspelled, or stale key raises `TypeError` at construction. Changing
`engine=` means replacing the engine configuration too; do not carry GEPA
reflection keys into AutoResearch, Meta-Harness, or best-of-N.

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

## 10. Agent runtime prerequisites are hard requirements

- The upstream agent engines use Claude; this plugin's maintained Claude-free
  profile uses `agent_backend="pi"` and the pinned fork's Pi runner.
- Pi requires `jq` and `curl` for AutoResearch, plus `bwrap` on Linux or
  `sandbox-exec` on macOS when `sandbox=True`.
- Pi's `--tools` allowlist is not an OS security boundary. A missing OS
  sandbox is a hard preflight failure; there is no silent unsandboxed fallback.
- Do not assume the local Codex proposer drives AutoResearch or Meta-Harness.
  It is a custom proposer for the `gepa` backend.
- Keep `run_dir` and `output_dir` outside the checkout when agents need a
  workspace or persistent diagnostics.

## Quick preflight checklist

- [ ] The string-candidate API and selected optimization mode match the goal.
- [ ] The evaluator returns a higher-is-better score and actionable feedback.
- [ ] `dataset`, `valset`, and `test_set` have the intended roles.
- [ ] The seed has failures the selected backend can learn from.
- [ ] `max_evals` is sized for many proposal rounds.
- [ ] `stop_at_score` and/or `max_token_cost` provide a real stop condition.
- [ ] `engine_config` keys match the selected backend exactly.
- [ ] The selected backend and proposer model have been tested with one call
      where live credentials are available.
- [ ] Pi/Codex credentials and OS sandbox prerequisites are present for the
      selected runtime.
- [ ] External run and output directories are configured when artifacts matter.
- [ ] A separate held-out score is reported when unbiased measurement matters.
