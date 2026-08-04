---
name: gepa-omni-skill
description: Optimize scorable text artifacts—prompts, code, configs, schemas, SQL, regexes, or agent instructions—with GEPA's evaluator-driven search. Use when tuning, comparing, or automatically improving a candidate from objective metrics, execution feedback, or LLM-as-judge scores.
---

# GEPA Omni skill

Use the engine-pluggable `gepa.optimize_anything.optimize_anything` API to search over a prompt, program, configuration, schema, SQL query, regular expression, or agent instruction. The evaluator returns a higher-is-better score and useful feedback.

## Workflow

1. Identify the seed candidate, evaluator, and any score ceiling.
2. Choose a mode: single-task, multi-task (`dataset`), or generalization (`dataset` plus held-out `valset`).
3. Return concrete failures, outputs, diffs, and partial-credit signals in evaluator `info`.
4. Choose an engine: `gepa`, `best_of_n`, `autoresearch`, or `meta_harness`. Use the composition helpers for Omni-style multi-engine search.
5. Set an explicit `max_evals` or `max_token_cost`, and keep `run_dir` and `output_dir` outside the checkout when agents need a workspace.
6. Run `python skills/gepa-omni-skill/scripts/preflight.py --engine <engine> --agent-backend pi` for the Claude-free agent profile before a long run.
7. Inspect `result.best_candidate`, `result.best_score`, `result.total_evals`, `result.eval_log`, and `result.metadata`. Use `test_set` for an unbiased held-out measurement.

## Engine selection

Use `OptimizeAnythingConfig(engine=...)` with the current upstream API:

- `gepa` — in-process reflective mutation and Pareto-aware selection. It is the engine that can use the local `CodexAgentProposer`.
- `autoresearch` — a long-horizon autonomous experiment loop. In the
  Claude-free profile, configure `agent_backend="pi"`; Ralph continuations
  stay in one persistent Pi RPC session.
- `meta_harness` — a proposer that emits several candidates while the outer
  framework evaluates and selects them. With `agent_backend="pi"`, each
  iteration gets a fresh Pi session while the frontier workspace persists.
- `best_of_n` — bounded independent proposal sampling.

The agent engines use their configured backend and an OS-level sandbox/workspace.
The supported Claude-free profile uses the maintained GEPA fork's Pi runner;
it does not silently fall back to Claude. See `references/api.md` and
`references/pi.md` for engine-specific options and composition examples.

## Codex proposer

For a Codex-backed loop, follow `references/codex.md` and use `scripts/codex_agent_proposer.py`. It materializes context, invokes `codex exec` read-only, validates `new_texts`, and preserves diagnostics.

For a repository self-analysis, use `scripts/self_evaluate.py` with an explicit model. It evaluates proposer candidates in a temporary fixture and stores GEPA artifacts outside the checkout.

### Local setup

The plugin includes a local `pyproject.toml` for its tests and linting. Set up
those tools with:

```bash
uv sync --project . --group dev
```

Install the maintained engine-capable `gepa[full]` fork in the evaluator's
environment. The fork URL and commit are deployment configuration, not a root
or plugin dependency of this repository. Supply that requirement explicitly
when running the plugin locally:

```bash
GEPA_OMNI_SPEC='gepa[full] @ git+https://<maintained-gepa-fork>/<org>/<repo>.git@<commit>'
uv run --project . --with "$GEPA_OMNI_SPEC" \
  python skills/gepa-omni-skill/scripts/preflight.py \
  --engine omni --agent-backend pi
```

For a local checkout of the fork, replace `GEPA_OMNI_SPEC` with its `file:`
requirement. The GEPA/Codex path requires an authenticated `codex`; the
Claude-free agent path requires authenticated `pi`, `jq`, and `curl` for
agent engines, plus `bwrap` on Linux or `sandbox-exec` on macOS when
sandboxing is enabled. See `references/codex.md` and `references/pi.md`.

## References

- `references/api.md` — published GEPA API, modes, budgets, proposer configuration, and result shape.
- `references/writing_evaluators.md` — evaluator contract, feedback design, judges, and averaging.
- `references/gotchas.md` — reward hacking, selection bias, saturated signals, and backend prerequisites.
- `references/tracking.md` — optional W&B/MLflow tracking and run artifacts.
- `references/codex.md` — Codex proposer setup, contract, diagnostics, and limits.
