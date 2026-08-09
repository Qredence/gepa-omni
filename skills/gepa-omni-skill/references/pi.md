# Backend compatibility labels

`agent_backend="pi"` remains available for callers that use the historical
Pi name. It uses the same OpenAI-compatible Chat Completions endpoint as the
`codex` and `claude` labels; no Pi executable or provider-specific login is
required.

Set the shared environment configuration:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-api-key"
```

## Native runner

```python
from native_omni import OpenAIChatCompletionRunner

runner = OpenAIChatCompletionRunner(backend="pi", timeout_seconds=600)
result = runner.run(
    prompt,
    work_dir="/tmp/gepa-pi-compatible-workspace",
    session_id="optional-session-id",
)
```

The runner keeps the backend label in result metadata, retains message history
for a continuing AutoResearch session, records usage/cost, and writes raw API
responses under the external workspace. The model does not execute local
tools, shell commands, or filesystem operations.

`PiAgentProposer` is the reflective GEPA compatibility alias for the same Chat
Completions proposer. `pi_model`, `pi_command`, and the old CLI runner classes
remain source-compatible options, but `OPENAI_MODEL` and the shared API
environment are authoritative.

## Preflight

```bash
uv run python skills/gepa-omni-skill/scripts/preflight.py \
  --engine autoresearch --agent-backend pi
```

Preflight checks `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, the
shared native runner, and configured pricing when a token cap is supplied. It
does not probe a Pi installation or make a model call unless `--test-lm` is
provided.
