# Security policy

GEPA Omni fails closed around endpoint credentials:

- official OpenAI hosts may use `OPENAI_API_KEY`;
- other hosts require `GEPA_OMNI_API_KEY` or an explicitly selected provider
  variable;
- remote endpoints require HTTPS;
- HTTP is allowed only for loopback;
- embedded URL credentials and credential-like query parameters are rejected;
- private, link-local, and cloud-metadata addresses require explicit opt-in;
- diagnostics redact keys, authorization headers, query strings, raw responses,
  and sensitive provider details.

Raw prompt/response retention is disabled by default. Set
`GEPA_OMNI_RETAIN_RAW_RESPONSES=1` only for a controlled diagnostic and keep
the external run directory private. Do not paste a key into chat or write
prompted values to `.env`, shell profiles, or the repository.

Public optimization calls serialize endpoint resolution and temporary upstream
environment changes within this package. This lock does not synchronize unrelated
application code that changes `os.environ`. Endpoint connections use validated
numeric addresses, retain the URL hostname for TLS verification, and refuse
redirects and environment-configured proxies.
