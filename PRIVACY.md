# Privacy notice

Qredence's GEPA marketplace plugins are local-first developer tooling. The
repository does not operate a telemetry or hosted optimization service for
these plugins, and the plugin code does not send usage reports to Qredence.

When an optimization run uses a provider, the candidate, evaluator inputs, and
the prompts needed for that run may be sent to the endpoint configured through
the current process environment. The supported provider settings are
`OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY`. Other providers use
`GEPA_OMNI_API_KEY` or `GEPA_OMNI_API_KEY_ENV`, which names the environment
variable containing the provider key. The plugins do not ask
users to paste credentials into chat or intentionally persist the API key.

Run directories and optimizer artifacts are created at paths selected by the
caller and should be kept outside the repository checkout. The configured
provider's privacy policy and terms govern data sent to that provider. Users
remain responsible for checking their organization's data-handling requirements
before optimizing confidential material.

This notice describes the behavior of the open-source project; it is not legal
advice. Please open an issue in the repository if the implementation no longer
matches this notice.
