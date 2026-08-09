# Omni workflow

Omni is the plugin's default orchestration layer. It runs the standalone
`gepa==0.1.4` PyPI reflective GEPA engine beside the plugin-native AutoResearch
and Meta-Harness engines, chooses the strongest Phase 1 candidate, then starts
a fresh Phase 2 continuation. The plugin-native `best_of_n` engine is the
independent comparison baseline for standalone runs. `omni` is a preflight
target and workflow mode, not a public PyPI `engine=` value.

## Phases

```text
seed + evaluator
       |
       +--> Phase 1: gepa (PyPI reflective engine)
       +--> Phase 1: autoresearch (plugin-native)
       +--> Phase 1: meta_harness (plugin-native)
                    |
             best Phase 1 candidate
                    |
       Phase 2: fresh continuation (gepa by default)
                    |
             candidate + scores + held-out report
```

All Phase 1 branches share the same seed, objective, evaluator, dataset, and
selection `valset`. `test_set` is removed from every Phase 1 task and is used
only for final held-out scoring by the continuation/wrapper. Phase 2 starts a
new external run/output directory rather than resuming a Phase 1 branch.

## Budget partitioning

```python
from omni_pipeline import run_omni

result = run_omni(
    seed_candidate,
    task=task,
    max_evals=40,
    max_token_cost=20.0,
    run_dir="/tmp/omni-run",
    output_dir="/tmp/omni-output",
    continuation_engine="gepa",
)
```

The total evaluation and token budgets are divided into three exploration
slices and one continuation slice; evaluation remainders go to the
continuation. An explicit positive `max_evals` and/or `max_token_cost` is
required. When `max_evals` is the only bound, at least four evaluations are
needed so every phase receives a positive slice. Use a standalone engine when
the budget cannot support four phases.

## Continuation choices

The default continuation is `gepa`, using the PyPI reflective engine and the
read-only Chat Completions proposer. Set `continuation_engine="autoresearch"` or
`continuation_engine="meta_harness"` to use a plugin-native agent continuation.
Standalone `run_optimization(..., engine=...)` bypasses Omni and receives the
full supplied budget.

## Backend and model selection

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

`agent_backend` remains a compatibility label for `codex`, `pi`, or `claude`.
`OPENAI_MODEL` is authoritative for all branches; there is no provider-specific
model or CLI login resolution.

The interactive skill asks only for a missing model or base URL, applies those
answers to the current process, and does not persist them. The API key must be
provided through the environment or a secret manager and is never requested in
chat. Preflight remains non-interactive and validates the final configuration
before Omni starts.

The wrapper also preserves `codex_command`, `pi_command`,
`codex_timeout_seconds`, `codex_input_cost_per_million`,
`codex_output_cost_per_million`, `max_concurrency`,
`gepa_parallel_proposals`, and `stop_at_score`. Codex input/output rates are
required together when a Codex token cap is configured.

## Runtime prerequisites

Run the local preflight before a real Omni run:

```bash
python3 skills/gepa-omni-skill/scripts/preflight.py --engine omni
python3 skills/gepa-omni-skill/scripts/preflight.py \
  --engine omni --agent-backend pi
```

Preflight verifies the published GEPA API, plugin-native runtime primitives,
and the three `OPENAI_*` variables. `sandbox=False` is rejected at the wrapper
boundary, and the Chat Completions model receives no local tools.

Keep every `run_dir` and `output_dir` absolute and outside the checkout. Native
evaluation artifacts include per-evaluation JSON, `eval_trace.jsonl`, progress,
and a serializable `result.json`.
