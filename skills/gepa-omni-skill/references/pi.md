# Pi-backed agent engines

The Claude-free Omni profile uses the maintained GEPA fork's generic
`AgentRunner` extension. It does not copy AutoResearch or Meta-Harness into
the plugin and it does not silently fall back to Claude.

## Configuration

```python
from gepa.optimize_anything import OptimizeAnythingConfig

autoresearch = OptimizeAnythingConfig(
    engine="autoresearch",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    run_dir="external-runs/autoresearch",
    output_dir="external-runs/autoresearch/output",
    engine_config={
        "agent_backend": "pi",
        "pi_command": "pi",
        "model": "provider/model",
        "ralph": True,
        "max_no_eval_seconds": 300,
    },
)

meta_harness = OptimizeAnythingConfig(
    engine="meta_harness",
    max_evals=100,
    max_token_cost=5.0,
    sandbox=True,
    run_dir="external-runs/meta-harness",
    output_dir="external-runs/meta-harness/output",
    engine_config={
        "agent_backend": "pi",
        "pi_command": "pi",
        "model": "provider/model",
        "max_iterations": 20,
        "max_candidates_per_iter": 3,
    },
)
```

The plugin's `PiAgentProposer` is separate from these engines. It uses Pi JSON
mode with `--no-session`, `--no-context-files`, disabled extensions/skills,
and the read-only tool profile `read,grep,find,ls`. It preserves the GEPA
custom proposer contract and stores one diagnostic directory per proposal.

AutoResearch uses one persistent Pi RPC process, so Ralph continuation prompts
share the same live session state. Meta-Harness starts a new Pi process for
each iteration, while `agents/`, `frontier.json`, evaluation traces, and the
candidate workspace remain persistent.

## Safety and prerequisites

Pi's `--tools` allowlist is not an OS security boundary. With `sandbox=True`,
the fork wraps Pi in Linux `bwrap` or macOS `sandbox-exec`; if the requested
runtime is missing, preflight fails rather than launching Pi unsandboxed.
The jail exposes the temporary agent workspace, Pi's provider configuration,
system runtime files, and network access needed by the evaluator and model
provider. It does not expose the repository checkout as a writable path.

Install the fork externally and pin its URL and commit in the consuming
environment. The repository root intentionally does not add GEPA as a
dependency. Follow the canonical [local setup](../SKILL.md#local-setup),
using `--engine omni --agent-backend pi` for this profile.

For a deployed fork, replace the file URL with the maintained Git URL and
commit. Authenticate `pi` and its selected provider first. The agent-engine
preflight requires `jq` and `curl`; Linux sandboxing requires `bwrap`; macOS
uses the system Seatbelt `sandbox-exec` runtime.
