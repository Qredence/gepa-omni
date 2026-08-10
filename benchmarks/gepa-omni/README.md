# GEPA Omni benchmark harness

`benchmark.py` defines the equal-budget benchmark contract for prompt,
deterministic code/config, and noisy LLM-as-judge tasks. The credential-free
fixture runs five seeds for each standalone engine and Omni, and emits median,
variance, cost, evaluation, time-to-target, duration, failure, target, and
selection-to-test gap fields.

Run the local smoke benchmark:

```bash
uv run python benchmarks/gepa-omni/benchmark.py --output /tmp/gepa-omni-benchmark.json
```

Live adapters may replace `fixture_runner` after explicit credential and model
setup. They must preserve the same seed, split, evaluation budget, proposer
cost cap, and report schema. CI runs only the deterministic fixture.
