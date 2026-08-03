# Gotchas and pitfalls

Read before any real run. The current launcher can select different engines,
but every engine still optimizes exactly the score and feedback returned by
your evaluator.

## 1. Reward hacking

GEPA maximizes exactly the score you compute. If the metric is gameable, any
engine can exploit it. Use gated objectives, validate the winning candidate
against the real goal, and include diagnostic side information instead of
relying on a correctness-only proxy.

## 2. Selection bias

The selected candidate is chosen on the training/validation task. With a small
or noisy validation set, the best score is optimistic. Use `test_set` for a
separate held-out report; the new launcher keeps it outside the optimization
server.

## 3. Stochastic evaluations

The default evaluates each candidate/example once. If the system under test is
stochastic, average multiple samples inside the evaluator so selection is not
driven by a lucky single result.

## 4. Evaluation and token budgets

Set `OptimizeAnythingConfig.max_evals`, `max_token_cost`, or both. The
evaluation budget limits calls to the shared evaluation server. The token-cost
budget limits the optimizer's own reflection or agent model spend. They are
separate budgets.

The Codex adapter's `timeout_seconds` limits one proposer subprocess; it does
not limit total run wall time. The upstream agent engines have their own
iteration/no-progress controls in `engine_config`.

## 5. Engine-specific runtimes

- `gepa` can use LiteLLM or the local `CodexAgentProposer`.
- `autoresearch` and `meta_harness` use the configured agent backend. The
  Claude-free plugin profile requires `agent_backend="pi"`; AutoResearch then
  keeps one Pi RPC session for Ralph and Meta-Harness starts a fresh session
  per iteration.
- Pi also needs `jq` and `curl` for AutoResearch, plus `bwrap` on Linux or
  `sandbox-exec` on macOS when `sandbox=True`. Pi's tool allowlist is not a
  security boundary, so a missing OS runtime is a hard preflight failure.
- `run_dir` and `output_dir` should point outside the checkout when an agent
  engine is allowed to edit its temporary candidate workspace.

Do not assume the local Codex adapter drives `autoresearch` or `meta_harness`.

## 6. Candidate shape

The `gepa` engine supports a dictionary of named components. The other built-in
engines treat the seed as one text value. Use a string seed when comparing
engines in a composition pipeline.

## 7. Composition budgets

Composition helpers run multiple engines and may run them concurrently. Give
each stage explicit limits and reserve enough evaluation budget for the final
continuation stage. Use a shared external output location so every stage's
result and diagnostics can be compared.

## 8. Baseline saturation

If the seed already scores at the ceiling on the visible examples, proposals
may be rejected and the seed may be returned unchanged. Include examples that
expose useful failure signals and inspect accepted proposals, not just proposal
count.

## 9. Evaluator exceptions

Catch expected failures and return a low score with `info["error"]` and
concrete feedback so the active engine can learn from them. Do not silently
turn unexpected exceptions into success-shaped results.

## Quick preflight checklist

- [ ] The task mode and selected engine match the goal.
- [ ] The evaluator returns a higher-is-better score and actionable feedback.
- [ ] At least one explicit evaluation or token budget is set.
- [ ] The seed shape is supported by every engine in the pipeline.
- [ ] The selected agent backend (`pi` for the Claude-free profile) is authenticated and on `PATH`.
- [ ] `sandbox=True` prerequisites are available for agent engines.
- [ ] External `run_dir` and `output_dir` are set when artifacts must persist.
- [ ] A separate held-out score is reported when unbiased measurement matters.
