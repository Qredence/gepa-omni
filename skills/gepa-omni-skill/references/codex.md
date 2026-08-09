# Chat Completions runtime

The historical Codex names remain public compatibility surfaces, but model
calls no longer invoke a provider CLI. `CodexAgentProposer` and the native
agent engines use the same OpenAI-compatible Chat Completions endpoint.

Configure it once for every engine:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

`OPENAI_MODEL` is authoritative. The legacy `agent_model`, `codex_model`, and
backend command parameters remain accepted for source compatibility, but they
do not select a different provider or executable.

## Read-only GEPA proposer

`CodexAgentProposer` implements:

```python
proposer(
    candidate,
    reflective_dataset,
    components_to_update,
    *,
    metadata=None,
) -> dict[str, str]
```

Each call creates a unique external diagnostics directory containing the
candidate, reflective data, requested component names, request, response,
usage, and validation errors. The request is a JSON Chat Completions call with
`response_format={"type": "json_object"}`. The response must contain
`new_texts` with exactly the requested keys and string values.

Pass `input_cost_per_million`, `output_cost_per_million`, and
`max_token_cost` when a PyPI GEPA run is cost-bounded. `sandbox=False` is
rejected, and the plugin checkout is never used for proposal artifacts.

## Native agent runner

The production native runner is `OpenAIChatCompletionRunner`:

```python
from native_omni import OpenAIChatCompletionRunner

runner = OpenAIChatCompletionRunner(backend="codex", timeout_seconds=600)
result = runner.run(
    prompt,
    work_dir="/tmp/gepa-native-workspace",
    max_token_cost=5.0,
)
```

The runner retains Chat Completions message history for AutoResearch
continuations, records usage/cost, and writes the raw response to the external
workspace. The model receives text and JSON in the request; it does not receive
local shell or filesystem tools. `sandbox=True` is still mandatory at the
wrapper boundary, and external `run_dir`/`output_dir` paths are still required.

`CodexAgentRunner` is retained only for callers that directly depend on the
old subprocess class. The plugin pipeline does not construct it.

## Preflight

Run preflight without making a model call:

```bash
uv run python skills/gepa-omni-skill/scripts/preflight.py --engine codex
uv run python skills/gepa-omni-skill/scripts/preflight.py \
  --engine autoresearch --agent-backend codex \
  --codex-input-cost-per-million 2 \
  --codex-output-cost-per-million 8
```

Preflight validates the pinned PyPI GEPA API where relevant, the native
Chat Completions runner, and all three `OPENAI_*` variables. It does not send a
prompt unless `--test-lm` is supplied.
