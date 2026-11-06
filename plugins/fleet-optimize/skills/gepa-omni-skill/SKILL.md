---
name: gepa-omni-skill
description: >-
  Improve scorable prompts, programs, configs, specs, SQL, schemas, regexes,
  and agent instructions with GEPA Omni. Use when a candidate can be scored by
  an objective evaluator or useful LLM-as-judge feedback.
---

# GEPA Omni

Use the stable `gepa_omni` package for orchestration. This skill ships in the
Fleet Optimize plugin and does not share a runtime or API with
`fleet-optimize-skill`.

The latest upstream GEPA supplies the standalone reflective engine; this
plugin adds isolated native adapters and orchestration around that contract.

## Choose a strategy

Use the public API for Python callers:

```python
from gepa_omni import OmniConfig, optimize

result = optimize(
    seed_candidate=seed,
    evaluator=evaluate,
    dataset=train,
    valset=validation,
    test_set=test,
    config=OmniConfig(strategy="auto", max_evals=400),
)
```

The evaluator must return a higher-is-better scalar, optionally paired with a
feedback dictionary. Include failures, outputs, diffs, and partial-credit
signals in the feedback so a proposer can improve the next candidate.

Choose one of three strategies:

| Strategy | Use when |
| --- | --- |
| `gepa` | You need a predictable standalone reflective run. |
| `omni` | You are intentionally running a fixed portfolio experiment. |
| `auto` | You want small pilots followed by adaptive allocation. |

`auto` evaluates the seed, pilots eligible engines, compares improvement per
evaluation and per dollar, and gives the remaining budget to the strongest
engine or engines. Small budgets fall back to standalone GEPA or Best-of-N.
`omni` is not the default and requires enough budget for its fixed phases.

Keep `test_set` untouched until final reporting. `dataset` and `valset` may be
used for optimization and selection; held-out examples must never influence
engine selection or budget allocation.

## Safe workflow

1. Define a string seed candidate and a feedback-rich evaluator.
2. Choose representative training and validation examples.
3. Set an explicit `max_evals` and/or `max_token_cost`.
4. Run preflight before a live run.
5. Keep `run_dir` and `output_dir` absolute and outside the checkout.
6. Inspect the normalized result, selection score, held-out score, cost, and
   evaluation log separately.

The package accepts `gepa_omni.run_optimization(...)` for callers that already
have a task mapping. Do not import `scripts/omni_pipeline.py`; that file is a
temporary compatibility shim for the plugin runtime.

## Evaluator contract

```python
def evaluate(candidate: str, example: object) -> tuple[float, dict]:
    output = run_system(candidate, example)
    return grade(output, example), {"output": output}
```

This is the dataset form. For a single task, omit `dataset` and `valset`; the
evaluator then takes only `candidate` and scores it directly:

```python
def evaluate(candidate: str) -> tuple[float, dict]:
    output = run_system(candidate)
    return grade(output), {"output": output}
```

For multiple examples, use a representative `valset` for selection. For
stochastic systems, average several samples inside the evaluator rather than
selecting a lucky one.

The direct upstream launcher is also available when only standalone GEPA is
needed:

```python
from gepa.optimize_anything import (
    EngineConfig,
    GEPAConfig,
    ReflectionConfig,
    optimize_anything,
)

result = optimize_anything(
    seed_candidate="candidate text",
    evaluator=evaluate,
    objective="Improve the candidate against the evaluator.",
    config=GEPAConfig(
        engine=EngineConfig(max_metric_calls=20),
        reflection=ReflectionConfig(reflection_lm=model),
    ),
)
```

Read `references/api.md` for typed API and result details, `references/engines.md`
for engine behavior and allocation, and `references/omni.md` for fixed portfolio
phases and continuation. Read `references/providers.md` before configuring a
model endpoint and `references/security.md` before using a private provider.

## Credentials and runner profiles

All current engines use the same OpenAI-compatible Chat Completions runner. The
model receives no local shell or filesystem tools. `runner_profile` is metadata
for compatibility presets; it does not launch Codex, Pi, or Claude CLIs and it
does not imply a writable workspace session.

For the official OpenAI endpoint:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-key"
```

For another provider, use `GEPA_OMNI_API_KEY` or a provider-specific variable;
never send `OPENAI_API_KEY` to that endpoint. Remote endpoints must use HTTPS;
HTTP is allowed only for loopback. Embedded URL credentials and credential-like
query parameters are rejected. Private or metadata addresses require explicit
opt-in.

Never ask the user to paste `OPENAI_API_KEY` into chat. Configure it through
the environment or a secret manager. Do not write prompted values to `.env` or
the repository. The setup layer asks only for missing
non-secret model and URL values, keeps them process-scoped, and never writes them to `.env` or the
repository. Preflight remains non-interactive and does not make a model call unless
`--test-lm` is explicitly requested.

Run from the repository root with the development environment:

```bash
uv run python plugins/fleet-optimize/skills/gepa-omni-skill/scripts/preflight.py --engine gepa
uv run python plugins/fleet-optimize/skills/gepa-omni-skill/scripts/preflight.py --engine omni
```

Raw prompts and responses are not retained by default. Set
`GEPA_OMNI_RETAIN_RAW_RESPONSES=1` only when a controlled diagnostic requires
them, and keep the external run directory private.

The adapted native runtime provenance is pinned to
`8a2bed96385202f69caaeb5327a843ed2f5ea225`; see `THIRD_PARTY_NOTICES.md`.

## References

- `references/api.md` — public package and upstream GEPA contracts.
- `references/engines.md` — standalone engines and adaptive selection.
- `references/providers.md` — endpoint setup and model configuration.
- `references/omni.md` — fixed portfolio phases and budgets.
- `references/security.md` — credential, URL, and artifact-retention policy.
- `references/writing_evaluators.md` — feedback-rich evaluator patterns.
