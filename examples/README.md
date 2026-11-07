# Support-ticket prompt optimization

`optimize_support_prompt.py` runs the GEPA Omni plugin-native automatic strategy
against a small, labeled support-routing task. It compares an underspecified
starting prompt with the selected prompt on validation and held-out examples.
The toy data demonstrate the workflow; six held-out tickets are insufficient
for a production accuracy claim.

From the repository root:

```bash
uv sync --locked --group dev
uv run python examples/optimize_support_prompt.py \
  --env-file .env \
  --max-evals 120 \
  --input-per-million 0.10 \
  --cached-input-per-million 0.01 \
  --output-per-million 0.50
```

This makes paid calls using the configured endpoint and model. Omit `--env-file`
to use the existing process environment. An explicitly supplied environment file
is read without overriding existing environment variables; it is never modified.
Configure credentials through your environment or secret manager.

The rates above are the standard GPT-6 Luna text-token rates checked on
2026-11-07 at https://developers.openai.com/api/docs/models/gpt-6-luna.
Supply rates appropriate to your configured model. Estimates account for reported
cached-input tokens and include output reasoning tokens through the provider's
completion-token count. They are not a billing statement and exclude any
account-specific pricing, regional premiums, or other provider adjustments.

The optimizer has a maximum of 120 example evaluations, including its seed
baseline. It can stop before using that allowance. Four separate reporting
passes use another 24 example calls: initial validation, final validation,
initial held-out test, and final held-out test. Held-out examples are used only
after optimization ends. Proposer calls consume additional tokens but are not
example evaluations.

Artifacts default to an external temporary directory. Use `--output` to choose
an external persistent directory. The script records:

- `best-prompt.txt`: the actual selected prompt.
- `report.json`: before/after scores, evaluation counts, and estimated cost.
- `usage.json`: per-call token usage for both evaluation and proposal calls.
- `evaluations.json`: observed scores and diagnostic answers, without keys.
- `runs/` and `outputs/`: optimizer state and diagnostics.

The native plugin's existing proposer and runner factory hooks attach usage
accounting to every model request. Connections retain the plugin's pinned-endpoint
transport. The script loads the checkout's package and runtime explicitly, so it
uses local changes even when an older package is installed.

Score 1 means the response is exactly the correct lowercase category after
trimming surrounding whitespace. Explanations, invalid categories, and incorrect
labels receive 0. Feedback exposes the expected label and failure to guide search.

Compare validation and held-out scores separately. A validation improvement with
no held-out improvement is not evidence of better generalization. Once test
failures guide further revisions, use fresh held-out examples for final reporting.

## Observed run — 2026-11-07

Using the configured `gpt-6-luna` model and a 120-evaluation optimizer budget:

| Measurement | Starting prompt | Selected prompt |
| --- | --- | --- |
| Validation exact-label accuracy | 0/6 (0%) | 6/6 (100%) |
| Held-out exact-label accuracy | 0/6 (0%) | 6/6 (100%) |

Auto selected AutoResearch for continuation. The run used exactly 120 optimizer
evaluations, plus 24 reporting evaluations, and 18 proposer calls: 162 model
requests in total. Recorded usage was 132,502 input tokens (96,800 cached) and
10,877 output tokens. At the standard rates above, estimated cost was
$0.0099767: $0.0037801 for evaluations and $0.0061966 for proposals.
These are observed results from this small demonstration, not guaranteed results
or evidence of production-wide accuracy. The starting prompt's zero score reflects
the exact-output-format requirement as well as category correctness.

## Harder benchmark

`support_routing_hard.json` fixes a stronger starting prompt, a detailed routing
policy, and disjoint, class-balanced splits: 12 training, 12 validation, and
60 held-out tickets (20 per category). Tickets are human-authored synthetic
examples, not sampled production data. They cover overlapping departments,
permission denials versus regressions, broken financial/account workflows,
resolved incidental issues, and misleading ticket-template labels.

```bash
uv run python examples/optimize_support_prompt.py \
  --env-file .env \
  --dataset examples/support_routing_hard.json \
  --max-evals 120 \
  --report-concurrency 4 \
  --input-per-million 0.10 \
  --cached-input-per-million 0.01 \
  --output-per-million 0.50
```

The reporting concurrency affects only the independent before/after measurements;
optimizer concurrency remains one. The evaluation budget remains 120. Separate
reporting uses 144 calls (two validation passes of 12 and two test passes of 60).
The complete dataset snapshot and its SHA-256 hash are saved with each report.
No held-out examples or results are supplied to the optimizer. Do not tune this
benchmark's policy or labels after inspecting its held-out results.

### Observed harder-benchmark result — 2026-11-07

With `gpt-6-luna`, both the stronger starting prompt and the selected prompt
scored 12/12 on validation and 60/60 on held-out tickets. Every held-out category
scored 20/20 before and after. The optimizer retained the starting prompt
unchanged: this run demonstrated no measurable optimization gain.

The run used exactly 120 optimizer evaluations plus 144 reporting evaluations,
and 10 proposal calls (274 model calls total). Recorded usage was 40,062 input
tokens, no reported cached-input tokens, and 11,945 output tokens. Estimated cost
at the rates above was $0.0099787: $0.0073629 for evaluations and $0.0026158 for
proposals. Dataset SHA-256:
`3e809fb1dfd6cb69c9db64d459bc2cd52f1ef7ceb3f98638872d70b4b26507bc`.

The authored edge cases did not defeat this model and starting prompt. These
results expose a benchmark ceiling, not proof of perfect production routing.
A future benchmark should use independently labeled, anonymized production
requests and unseen failure modes; its test set must be reserved for that run.
