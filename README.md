# GEPA Omni

GEPA Omni is a Codex plugin for improving scorable artifacts—prompts, code,
configurations, schemas, SQL, regular expressions, and agent instructions—
with evaluator-driven search from [GEPA](https://github.com/gepa-ai/gepa).

The plugin includes engine-pluggable workflows, Codex and Claude-free Pi
proposers, read-only proposal isolation, and diagnostics for every proposal.

## Repository layout

| Path | Purpose |
| --- | --- |
| `.codex-plugin/plugin.json` | Plugin manifest and marketplace metadata |
| `skills/gepa-omni-skill/` | Installed skill, references, and proposer scripts |
| `tests/` | Deterministic proposer and preflight tests |
| `tools/` | Development-only staging and self-evaluation helpers |
| `LICENSE` | MIT license text |

## Quick start

Requirements:

- Python 3.10 or newer
- [`uv`](https://docs.astral.sh/uv/)
- An engine-capable `gepa[full]` environment supplied by the consumer
- `codex` for the Codex proposer, or an authenticated `pi` plus its runtime
  prerequisites for the Claude-free profile

Install the development tools and run the deterministic checks:

```bash
uv sync --project . --group dev
uv run pytest -q
git diff --check
```

Run the preflight for an engine before a long optimization:

```bash
python3 skills/gepa-omni-skill/scripts/preflight.py --engine codex
python3 skills/gepa-omni-skill/scripts/preflight.py --engine omni --agent-backend pi
```

The engine-capable GEPA fork is intentionally not vendored or declared as a
root dependency. Supply it explicitly when needed:

```bash
export GEPA_OMNI_SPEC='gepa[full] @ git+https://<maintained-gepa-fork>/<org>/<repo>.git@<commit>'
uv run --project . --with "$GEPA_OMNI_SPEC" \
  python3 skills/gepa-omni-skill/scripts/preflight.py \
  --engine omni --agent-backend pi
```

See the skill references for the [API](skills/gepa-omni-skill/references/api.md),
[Codex proposer](skills/gepa-omni-skill/references/codex.md),
[Pi proposer](skills/gepa-omni-skill/references/pi.md), and
[evaluator design](skills/gepa-omni-skill/references/writing_evaluators.md).

## Staging the installable plugin

The development checkout contains tests and tooling that are not shipped.
Build the runtime payload into an empty directory outside the checkout:

```bash
python3 tools/stage_plugin.py --output /tmp/gepa-omni
```

The staged payload contains only `.codex-plugin/`, `skills/`, and `LICENSE`.

## Development conventions

- Keep the manifest name, skill name, and staging output aligned as `gepa-omni`
  and `gepa-omni-skill`.
- Keep proposal artifacts and evaluation run directories outside the checkout.
- Do not commit credentials, model outputs, `.plugin-eval/` data, or generated
  Python caches.
- Validate changes with the focused tests, `git diff --check`, and the
  relevant preflight or staging command.

## License

GEPA Omni is distributed under the [MIT License](LICENSE).
