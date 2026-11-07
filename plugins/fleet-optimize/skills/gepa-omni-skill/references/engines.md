# Engines and allocation

`gepa` is the standalone reflective engine from upstream GEPA. `autoresearch`,
`meta_harness`, and `best_of_n` are plugin-native adapters using the same
evaluator and budget contracts. All current model calls use Chat Completions.

Use the explicit strategy values:

- `gepa`: predictable standalone run;
- `omni`: fixed three-engine portfolio plus continuation;
- `auto`: seed evaluation, small pilots, and adaptive continuation.

`auto` keeps the test set out of selection. It falls back to GEPA when the
budget cannot support one pilot per eligible engine plus continuation. Ranking
uses improvement per evaluation when the seed can be scored and otherwise uses
the pilot score per evaluation. The result metadata records pilot scores and
the selected continuation engine.

`best_of_n` is the independent comparison floor: it samples candidates without
using feedback history. Use it for baselines, not as a replacement for a
feedback-rich evaluator.

Clean-package automatic runs pilot GEPA and Best-of-N using pinned endpoint
transports. Standalone upstream AutoResearch and MetaHarness launch external
agent processes that cannot enforce this endpoint policy, so they fail before
launch; install the plugin to use their native Chat Completions implementations.
