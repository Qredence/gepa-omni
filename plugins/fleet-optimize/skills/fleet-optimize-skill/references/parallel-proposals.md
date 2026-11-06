# Parallel proposals

One GEPA iteration samples a set of `(parent, minibatch)` tasks, reflects on
them, evaluates the resulting candidates, and applies a selection strategy.
With P×N sampling, P parents are selected and each parent receives N mutations,
so one iteration has `P * N` proposal tasks.

## Evaluation tiers

1. `parallel=False` is the deterministic option for evaluators with shared
   mutable state or limited resources.
2. `parallel=True` uses upstream GEPA's worker pool for singleton evaluations.
3. `batch_evaluator` receives the pending pairs as one grouped call and is the
   right seam for provider batch APIs or external schedulers.

The Fleet Optimize default enables parallel evaluation and uses `P=2`, `N=2`.
The top-level `max_concurrency` controls the shared evaluation server; the
helper also forwards the same value to the GEPA engine's worker setting unless
an explicit engine override is provided.

## Selection and acceptance

Sampling determines which proposals are attempted. Selection determines which
of the evaluated proposals enter the candidate pool. Built-in selection
strategies apply the configured acceptance criterion and deduplicate identical
children according to upstream GEPA behavior.

`AllImprovements` is the default because it preserves complementary gains on
the Pareto frontier. Use `BestImprovement` or `TopKImprovements` when the
candidate pool or validation cost needs to remain smaller.

## Budget behavior

The metric-call budget is shared across the proposal minibatch evaluations,
child evaluations, and accepted-candidate validation. A parallel iteration can
consume several proposal evaluations before the engine checks a stopping
condition, so a run may pass the nominal boundary by one full iteration.

Choose a width that matches the available evaluation capacity. More proposals
per step reduce the number of frontier-update rounds, but wider validation
stages can become the bottleneck when many candidates are accepted.

## Batch contract

```python
def batch_evaluator(pairs, opt_states=None):
    """Return one score or (score, info) for each input pair."""
```

The return list must have the same length and order as `pairs`. If a whole
batch fails, raise and let upstream GEPA apply its configured exception policy.
With `raise_on_exception=False`, GEPA substitutes `(0.0, {"error": ...,
"_gepa_transient_failure": True})` for every pair and does not cache those
results. Do not return ordinary `(score, info)` values for a failed batch,
because they are scored and cached as real evaluations; if the evaluator must
report the failure itself, set `"_gepa_transient_failure": True` in each
pair's `info`.
