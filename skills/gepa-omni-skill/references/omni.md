# Omni workflow — portfolio exploration plus fresh continuation

GEPA Omni's normal optimization workflow is the upstream Omni strategy: explore
with three different search engines, select the best result, then give that
winner to a fresh continuation optimizer. It is a portfolio-and-continuation
workflow, not a task classifier that guesses which single engine to use.

```text
shared seed + evaluator + objective + dataset/valset
                         │
           Phase 1: optimize_best_of (parallel)
          ┌──────────────┼────────────────┐
          │              │                │
       gepa        autoresearch      meta_harness
   Codex proposer   Codex, persistent Codex, fresh sessions
          └──────────────┼────────────────┘
                         │ highest-scoring best_candidate
                         ▼
           Phase 2: fresh optimize_anything
             default: gepa + Codex proposer
                         │
                         ▼
               final candidate + test_set report
```

## Default behavior

For a normal “optimize this” request, omit an engine override and use the
internal `scripts/omni_pipeline.py` orchestration. It calls the existing
`optimize_best_of` and `optimize_anything` composition primitives; it does not
introduce a public optimizer API.

The shared task is passed unchanged to all engines except that `test_set` is
removed from Phase 1. The Phase 1 winner is passed as the string seed of a new
Phase 2 config. The default continuation is fresh GEPA (`engine="gepa"`) with
`CodexAgentProposer`, so the default local flow is “Omni-GEPA”. A caller may
explicitly set `continuation_engine="autoresearch"` or
`continuation_engine="meta_harness"`; those continuations use the local Codex
backend by default. Pass `agent_backend="pi"` or `agent_backend="claude"` for
an explicit alternative.

Conceptually, the public composition calls are:

```python
explore = optimize_best_of(
    seed_candidate,
    evaluator=evaluator,
    dataset=dataset,
    valset=valset,
    objective=objective,
    configs=[gepa_config, autoresearch_codex_config, meta_harness_codex_config],
)

final = optimize_anything(
    explore.best_candidate,
    evaluator=evaluator,
    dataset=dataset,
    valset=valset,
    objective=objective,
    test_set=test_set,  # final reporting only
    config=fresh_continuation_config,
)
```

Each Phase 1 config has the same task and an independent external run/output
directory. `optimize_best_of` performs the parallel branch execution and
returns the highest-scoring branch winner. Phase 2 constructs a new optimizer
and new directories; it does not resume any Phase 1 engine state.

## Budget partitioning

Omni requires an explicit usable total budget: `max_evals`,
`max_token_cost`, or both. Each supplied budget is divided independently into
four balanced slices—one for each Phase 1 engine and one for the continuation.
For integer `max_evals`, an indivisible remainder is assigned to the
continuation so no evaluation allowance is lost. Every slice must be positive;
for example, an evaluation-only Omni run needs at least four evaluations. A
budget that cannot produce four positive slices should use an explicit
standalone engine instead.

`stop_at_score` is copied to each phase. It can end a phase early, but it does
not change the configured four-way allocation. Keep the total budget large
enough for meaningful proposal rounds; four tiny slices are a valid error
avoidance mechanism, not a useful optimization plan.

## Data-set boundaries

Use `dataset` and `valset` for optimization and selection according to the
standard launcher contract. Hold `test_set` out of all three Phase 1 calls and
include it only in the final Phase 2 call. Report its score separately from
the selection score. This prevents the portfolio winner from being selected on
the held-out result.

## Runtime substitutions

- `gepa`: `CodexAgentProposer`, with read-only proposal isolation and structured
  `new_texts` validation.
- `autoresearch`: `agent_backend="codex"` with one persisted Codex thread for
  Ralph-style continuation. Codex uses `--sandbox workspace-write` in the
  external engine workspace.
- `meta_harness`: `agent_backend="codex"` with fresh ephemeral Codex sessions
  per iteration and persistent frontier/workspace state.

Run `preflight.py --engine omni` before a live Omni run. The
`omni` value is a preflight target that checks the complete runtime surface; it
is not a value to pass as the launcher's `engine=`. OS sandbox prerequisites,
the maintained GEPA fork's Codex runner, Codex, and credentials must be
available in the consumer environment. When `max_token_cost` is configured,
also pass both Codex pricing rates to preflight. No model calls are made by
preflight unless explicitly requested.

## Standalone overrides

Use an explicit `engine="gepa"`, `engine="autoresearch"`,
`engine="meta_harness"`, or `engine="best_of_n"` when comparing one backend,
debugging a branch, or when the total budget is too small for four slices.
Standalone runs use the full supplied budget and bypass both Omni phases. The
plugin does not infer an engine from the task.

The upstream conceptual source for this two-phase strategy is the [Optimize
Anything Omni announcement](https://gepa-ai.github.io/gepa/blog/2026/07/22/optimize-anything-omni/).
