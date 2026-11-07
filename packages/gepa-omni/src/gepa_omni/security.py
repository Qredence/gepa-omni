"""Endpoint and credential policy for GEPA Omni model calls."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit


OFFICIAL_OPENAI_HOSTS = frozenset({"api.openai.com"})
PRIVATE_HOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data.ec2.internal",
    }
)


class EndpointPolicyError(ValueError):
    """Raised when endpoint or credential policy fails closed."""


def redact_diagnostic(value: object, *, secrets: tuple[str, ...] = ()) -> str:
    """Bound and redact provider details before they enter diagnostics."""

    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|password)[\"']?\s*[=:]\s*[\"']?)([^&\s\"',}]+)", r"\1[REDACTED]", text
    )
    return " ".join(text.split())[:500]


@dataclass(frozen=True)
class EndpointConfiguration:
    """Validated endpoint and the credential source selected for it."""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    key_source: str
    addresses: tuple[str, ...] = ()

    @property
    def endpoint(self) -> str:
        return chat_completions_url(self.base_url)


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _host_is_private(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized in PRIVATE_HOST_NAMES or normalized.endswith(".local") or normalized.endswith(".internal"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return not address.is_global


def _validate_url(base_url: str, *, allow_private: bool) -> tuple[str, str, bool, tuple[str, ...]]:
    value = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise EndpointPolicyError("endpoint URL or port is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EndpointPolicyError("endpoint must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointPolicyError("endpoint must not contain embedded credentials")
    if parsed.fragment:
        raise EndpointPolicyError("endpoint must not contain a URL fragment")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise EndpointPolicyError("endpoint must contain a hostname")
    is_loopback = False
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host == "localhost"
    if parsed.scheme == "http" and not is_loopback:
        raise EndpointPolicyError("remote endpoints must use HTTPS; HTTP is allowed only for loopback")
    if _host_is_private(host) and not (allow_private or is_loopback):
        raise EndpointPolicyError("private, link-local, and metadata endpoints require explicit opt-in")
    try:
        addresses = tuple(
            sorted(
                {
                    item[4][0]
                    for item in socket.getaddrinfo(
                        host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
                    )
                }
            )
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise EndpointPolicyError("endpoint hostname could not be resolved") from exc
    if not addresses:
        raise EndpointPolicyError("endpoint hostname resolved to no addresses")
    if not allow_private and not is_loopback and any(_host_is_private(address) for address in addresses):
        raise EndpointPolicyError("endpoint resolves to a private or non-public address; explicit opt-in is required")
    sanitized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    if parsed.query and any(
        token.lower() in parsed.query.lower() for token in ("key", "token", "secret", "password", "authorization")
    ):
        raise EndpointPolicyError("endpoint query strings must not contain credential-like parameters")
    return sanitized, host, host in OFFICIAL_OPENAI_HOSTS, addresses


def chat_completions_url(base_url: str) -> str:
    """Join ``/chat/completions`` without moving an existing query into the path."""

    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


_CALL_ENDPOINT: ContextVar[EndpointConfiguration | None] = ContextVar("gepa_omni_call_endpoint", default=None)


@contextmanager
def use_resolved_endpoint(endpoint: EndpointConfiguration):
    """Expose one already validated endpoint to nested plugin clients."""

    token: Token[EndpointConfiguration | None] = _CALL_ENDPOINT.set(endpoint)
    try:
        yield
    finally:
        _CALL_ENDPOINT.reset(token)


def bound_call_endpoint() -> EndpointConfiguration | None:
    """Return the public-API endpoint bound for this call, if any."""

    return _CALL_ENDPOINT.get()


def resolve_endpoint(
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    environ: dict[str, str] | None = None,
) -> EndpointConfiguration:
    """Resolve and validate endpoint credentials without leaking secrets.

    ``OPENAI_API_KEY`` is intentionally never selected for a non-OpenAI host.
    An explicit ``api_key`` or provider-specific variable is required there.
    """

    bound = _CALL_ENDPOINT.get()
    if bound is not None and base_url is None and api_key is None and api_key_env is None and environ is None:
        if model is None:
            return bound
        if not isinstance(model, str) or not model.strip():
            raise EndpointPolicyError("missing required endpoint configuration: OPENAI_MODEL")
        return replace(bound, model=model.strip())
    env = os.environ if environ is None else environ
    selected_url = base_url if base_url is not None else env.get("OPENAI_BASE_URL")
    selected_model = model if model is not None else env.get("OPENAI_MODEL")
    if not isinstance(selected_url, str) or not selected_url.strip():
        raise EndpointPolicyError("missing required endpoint configuration: OPENAI_BASE_URL")
    if not isinstance(selected_model, str) or not selected_model.strip():
        raise EndpointPolicyError("missing required endpoint configuration: OPENAI_MODEL")
    validated_url, _host, is_official_openai, addresses = _validate_url(
        selected_url,
        allow_private=_truthy(env.get("GEPA_OMNI_ALLOW_PRIVATE_ENDPOINTS")),
    )
    explicit_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
    provider_name = api_key_env or env.get("GEPA_OMNI_API_KEY_ENV")
    if not is_official_openai and provider_name == "OPENAI_API_KEY":
        raise EndpointPolicyError(
            "OPENAI_API_KEY is not permitted for this endpoint; configure GEPA_OMNI_API_KEY or a provider-specific key"
        )
    provider_key = env.get(provider_name) if provider_name else None
    if is_official_openai:
        selected_key = env.get("OPENAI_API_KEY") or explicit_key or provider_key
        key_source = (
            "OPENAI_API_KEY"
            if env.get("OPENAI_API_KEY")
            else "explicit"
            if explicit_key
            else provider_name or "provider"
        )
    else:
        selected_key = explicit_key or provider_key or env.get("GEPA_OMNI_API_KEY")
        key_source = "explicit" if explicit_key else provider_name if provider_key else "GEPA_OMNI_API_KEY"
    if not isinstance(selected_key, str) or not selected_key.strip():
        if not is_official_openai and env.get("OPENAI_API_KEY"):
            raise EndpointPolicyError(
                "OPENAI_API_KEY is not permitted for this endpoint; configure GEPA_OMNI_API_KEY or a provider-specific key"
            )
        raise EndpointPolicyError("missing endpoint API key; configure it through the environment or a secret manager")
    return EndpointConfiguration(validated_url, selected_model.strip(), selected_key.strip(), key_source, addresses)


__all__ = [
    "EndpointConfiguration",
    "EndpointPolicyError",
    "OFFICIAL_OPENAI_HOSTS",
    "redact_diagnostic",
    "bound_call_endpoint",
    "chat_completions_url",
    "resolve_endpoint",
    "use_resolved_endpoint",
]
