# Privacy notice

Qredence's GEPA marketplace plugins are local-first developer tooling. The
repository does not operate a telemetry or hosted optimization service for
these plugins, and the plugin code does not send usage reports to Qredence.

When an optimization run uses a provider, candidate text, evaluator inputs,
feedback, and prompts needed for the run may be sent to the configured
provider endpoint. GEPA Omni reads `OPENAI_BASE_URL` and `OPENAI_MODEL` from
the current process. The official OpenAI endpoint may use `OPENAI_API_KEY`;
other providers use `GEPA_OMNI_API_KEY` or set `GEPA_OMNI_API_KEY_ENV` to the
name of a provider-specific key variable. GEPA Omni does not send
`OPENAI_API_KEY` to another provider endpoint. Fleet Optimize instead follows
upstream GEPA's process-scoped `OPENAI_*` configuration contract; its
credential handling does not apply GEPA Omni's provider-key isolation. Use a
key scoped appropriately for the endpoint configured for that workflow.

The interactive GEPA Omni setup asks only for missing non-secret model or
base URL values, uses them for the current process, and does not write them to
`.env` or the repository. The plugin does not ask users to paste API keys into
chat. Raw prompt and response retention is disabled by default in the GEPA
Omni runtime; setting `GEPA_OMNI_RETAIN_RAW_RESPONSES=1` opts into that
diagnostic retention.

Run directories and optimizer artifacts use paths selected by the caller and
should be kept outside the repository checkout. They can contain optimizer
state, candidates, evaluator diagnostics, and data snapshots written by a
caller or example. The configured provider's privacy policy and terms govern
data sent to that provider. Users remain responsible for checking their
organization's data-handling requirements before optimizing confidential
material.

This notice describes the behavior of the open-source project; it is not legal
advice. Please open an issue in the repository if the implementation no longer
matches this notice.
