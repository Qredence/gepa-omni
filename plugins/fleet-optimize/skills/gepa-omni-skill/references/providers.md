# Providers and runner profiles

The production runner is one OpenAI-compatible Chat Completions client. The
model receives candidate context and JSON feedback but no local shell or
filesystem tools.

Official OpenAI configuration:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="your-model"
export OPENAI_API_KEY="your-key"
```

For another provider, keep the base URL and model in `OPENAI_BASE_URL` and
`OPENAI_MODEL`, but use `GEPA_OMNI_API_KEY` or set `GEPA_OMNI_API_KEY_ENV` to a
provider-specific environment variable. `OPENAI_API_KEY` is never selected for
a non-OpenAI host.

`runner_profile` replaces `agent_backend`. Values retained for compatibility
identify historical presets only; they do not launch Codex, Pi, or Claude CLIs.
The runner is not a writable coding-agent session.
