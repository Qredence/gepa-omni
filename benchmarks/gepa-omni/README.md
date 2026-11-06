# GEPA Omni benchmark harness

`benchmark.py` currently provides a credential-free schema smoke run for
prompt, deterministic code/config, and noisy LLM-as-judge case labels. It checks
that each engine/case/seed group can be represented in the report; it does not
run optimizers or evaluators. Performance fields are therefore `null` and must
not be used to compare engines.

Run the local smoke benchmark:

```bash
uv run python benchmarks/gepa-omni/benchmark.py --output /tmp/gepa-omni-benchmark.json
```

Live benchmark adapters must run the actual engines and evaluators, use the
same seed, data split, evaluation budget, and proposer cost cap, and populate
the performance fields from observed results. CI runs only the smoke fixture.
