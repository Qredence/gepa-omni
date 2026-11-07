# Fixed Omni portfolio

The latest upstream GEPA supplies the standalone reflective branch; Omni is
the plugin-native fixed portfolio around it.

If the model or endpoint configuration is missing, use the skill setup gate;
values remain process-scoped and preflight remains non-interactive.

Use `strategy="omni"` when the goal is a reproducible portfolio experiment.
The fixed portfolio runs GEPA, AutoResearch, and Meta-Harness pilots, selects
the strongest candidate on the selection data, and starts a fresh continuation.
The test set is unavailable to all exploration branches.

```python
from gepa_omni import OmniConfig, optimize

result = optimize(
    seed_candidate=seed,
    evaluator=evaluate,
    dataset=train,
    valset=validation,
    test_set=test,
    config=OmniConfig(
        strategy="omni",
        max_evals=40,
        max_token_cost=20.0,
        input_cost_per_million=2.0,
        output_cost_per_million=8.0,
    ),
)
```

The fixed strategy requires enough budget for three positive exploration
slices and a positive continuation slice. Use `strategy="auto"` for small or
uncertain budgets; it pilots eligible engines and gives the remainder to the
best improvement-per-evaluation option.

Keep run artifacts in private absolute directories outside the checkout. The
package records strategy, pilot, continuation, evaluation, and held-out
metadata in the normalized result.
