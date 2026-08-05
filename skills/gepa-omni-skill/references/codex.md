# Codex proposer

Requires external engine-capable `gepa[full]` and an authenticated `codex` on
`PATH`. Until the engine-pluggable API is available in the default package
index, install it with:

```bash
uv add "gepa[full] @ git+https://<maintained-gepa-fork>/<org>/<repo>.git@<commit>"
```

There are two deliberately separate Codex boundaries. `CodexAgentProposer` is
the read-only custom proposer used by the `gepa` engine. The maintained GEPA
fork also exposes a writable `CodexAgentRunner` for `autoresearch` and
`meta_harness`; those engines edit their external workspaces and run
`eval.sh`. The runner never falls back to Pi or Claude.

`CodexAgentProposer` implements GEPA's custom proposer contract:

```python
(candidate, reflective_dataset, components_to_update, *, metadata=None)
    -> dict[str, str]
```

It writes those inputs to a unique proposal directory, invokes `codex exec` in
`--sandbox read-only`, and requests:

```json
{"new_texts": {"component_name": "replacement"}, "summary": "text or null"}
```

Keys must match `components_to_update`; values must be strings. Proposal
directories retain inputs, schema, output, response, usage, and errors.

## Configure through the current launcher

The public `optimize_anything` launcher is string-candidate-first. The
component mapping in the example is the local compatibility boundary for the
pinned fork's custom-proposer hook: it gives `CodexAgentProposer` one named
component so the GEPA proposer contract can return `dict[str, str]`. Do not
use that mapping as the general launcher contract or when comparing engines.

```python
from codex_agent_proposer import CodexAgentProposer
from gepa.optimize_anything import (
    EngineConfig,
    OptimizeAnythingConfig,
    ReflectionConfig,
    optimize_anything,
)

proposer = CodexAgentProposer(
    run_dir="external-runs/my-gepa-run/proposer",
    model="<codex-model>",
    timeout_seconds=600,
)

result = optimize_anything(
    seed_candidate={"prompt": SEED_PROMPT},
    evaluator=evaluate,
    dataset=trainset,
    valset=valset,
    objective="Improve the prompt against the evaluator.",
    config=OptimizeAnythingConfig(
        engine="gepa",
        max_evals=300,
        stop_at_score=1.0,
        run_dir="external-runs/my-gepa-run/gepa",
        output_dir="external-runs/my-gepa-run/output",
        engine_config={
            "engine": EngineConfig(
                max_candidate_proposals=20,
                max_workers=1,
                parallel=False,
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

Use an explicit seed and an explicit `max_evals` or `max_token_cost`.
`timeout_seconds` defaults to 600 seconds per Codex subprocess and does not
limit total run time. USD cost is not inferred from Codex provider usage;
recognized token events are recorded.

The adapter isolates proposals with:

- `--ephemeral`
- `--ignore-user-config`
- `--skip-git-repo-check`
- `--sandbox read-only`
- `--output-schema`
- `--output-last-message`

It never grants write or unrestricted access to the Codex proposal process.
The proposer preserves `codex_stdout.jsonl`, `codex_stderr.log`,
`response.json`, `usage.json`, and `error.txt` under each proposal directory.

## Codex-backed AutoResearch and Meta-Harness

The plugin's Omni defaults route both writable agent engines through the fork's
Codex runner. Configure the two engine-specific blocks explicitly when using
the public launcher directly:

```python
codex_agent_settings = {
    "agent_backend": "codex",
    "codex_command": "codex",
    "model": "gpt-5-codex",  # omit to use the authenticated CLI default
    "codex_input_cost_per_million": 2.0,
    "codex_output_cost_per_million": 8.0,
}

autoresearch = OptimizeAnythingConfig(
    engine="autoresearch",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    engine_config={
        **codex_agent_settings,
        "ralph": True,
        "max_no_eval_seconds": 300,
    },
)

meta_harness = OptimizeAnythingConfig(
    engine="meta_harness",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    engine_config={
        **codex_agent_settings,
        "max_iterations": 20,
        "max_candidates_per_iter": 3,
        "timeout_seconds": 600,
    },
)
```

AutoResearch launches one non-ephemeral Codex thread and uses
`codex exec resume <thread_id>` for Ralph continuations. Meta-Harness launches
a fresh ephemeral thread for every iteration while retaining the shared
frontier and workspace. Each invocation keeps its command, thread id, JSONL
stdout, stderr, usage, completion state, and cost estimate under the external
run directory.

Codex always maps `sandbox=True` to `--sandbox workspace-write`. The runner
explicitly enables workspace-write network access so `eval.sh` can reach the
local evaluation server; this remains filesystem-sandboxed and does not grant
unrestricted host access. The workspace is the engine's external temporary/run
directory, never the plugin checkout. `sandbox=False` is rejected; it does not mean unrestricted Codex access. If
`max_token_cost` is set, both pricing rates are required and validated before
the process starts. If no USD cap is requested, usage-only runs remain valid;
when rates are absent their cost estimate is recorded as unknown rather than
reported as a real zero.

Pi and Claude remain available only when selected explicitly with
`agent_backend="pi"` or `agent_backend="claude"`. Pi uses its fork runner and
OS jail; the existing Claude paths retain their upstream behavior.

## Prerequisites

- Install `gepa[full]` in the evaluator's project.
- Keep `codex` on `PATH` and authenticate it before a live run.
- Give the proposer an external `run_dir` when proposal artifacts must persist.
- Use the repository self-evaluation harness with an explicit `--model` and an
  external `--run-dir`.

Run the preflight before a long run:

```bash
python skills/gepa-omni-skill/scripts/preflight.py --engine codex
python skills/gepa-omni-skill/scripts/preflight.py --engine omni
python skills/gepa-omni-skill/scripts/preflight.py --engine autoresearch --agent-backend pi
```

The preflight checks the current launcher API, the GEPA engine, the custom
proposer hook, the Codex executable, and the adapter contract. It does not
perform a model call unless a separate live smoke test is requested.
