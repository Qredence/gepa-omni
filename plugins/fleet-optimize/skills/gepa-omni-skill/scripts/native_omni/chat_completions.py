"""Small OpenAI-compatible Chat Completions client used by native engines.

The plugin deliberately talks to a Chat Completions compatible HTTP endpoint
instead of depending on a provider-specific CLI.  Production configuration is
read from ``OPENAI_BASE_URL`` and ``OPENAI_MODEL``. Official OpenAI hosts may
use ``OPENAI_API_KEY``; other endpoints require ``GEPA_OMNI_API_KEY`` or an
explicit provider key. The constructor accepts explicit values only to make
embedding and unit tests deterministic; environment values take precedence
when present.
"""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gepa_omni.security import (
    EndpointConfiguration,
    EndpointPolicyError,
    bound_call_endpoint,
    chat_completions_url,
    redact_diagnostic,
    resolve_endpoint,
)


class ChatCompletionError(RuntimeError):
    """Raised when an OpenAI-compatible Chat Completions request fails."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects so the bearer token stays on the approved host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "Chat Completions redirects are not followed", headers, fp)


def _urlopen_without_redirects(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_RejectRedirects).open(request, timeout=timeout)


Urlopen = Callable[..., Any]


def _configured(name: str) -> bool:
    value = os.environ.get(name)
    return isinstance(value, str) and bool(value.strip())


def _resolve_client_endpoint(
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> EndpointConfiguration:
    """Prefer a bound public-API endpoint, then environment configuration.

    Constructor arguments are compatibility hints for tests and embeddings.
    They apply only when this call has no bound endpoint and the corresponding
    environment variable is unset. An environment URL also selects that
    endpoint's credential, so a constructor key cannot follow the operator's host.
    """

    if bound_call_endpoint() is not None:
        return resolve_endpoint()
    environment_url = _configured("OPENAI_BASE_URL")
    return resolve_endpoint(
        base_url=None if environment_url else base_url,
        model=None if _configured("OPENAI_MODEL") else model,
        api_key=None if environment_url else api_key,
        api_key_env=None if environment_url else api_key_env,
    )


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return "".join(
            item.get("text", "") for item in value if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        )
    return ""


def _finite_non_negative(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return result


@dataclass(frozen=True)
class ChatCompletionResult:
    """Normalized Chat Completions response and usage telemetry."""

    final_text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    response: dict[str, Any]


class OpenAIChatCompletionsClient:
    """Call an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_seconds: float = 600.0,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        opener: Urlopen | None = None,
    ) -> None:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        try:
            endpoint = _resolve_client_endpoint(
                base_url=base_url,
                model=model,
                api_key=api_key,
                api_key_env=api_key_env,
            )
        except EndpointPolicyError as exc:
            raise ValueError(str(exc)) from exc
        self.base_url = endpoint.base_url
        self.model = endpoint.model
        self.api_key = endpoint.api_key
        self.key_source = endpoint.key_source
        self.timeout_seconds = timeout
        self.input_cost_per_million = (
            _finite_non_negative(input_cost_per_million, label="input_cost_per_million")
            if input_cost_per_million is not None
            else None
        )
        self.output_cost_per_million = (
            _finite_non_negative(output_cost_per_million, label="output_cost_per_million")
            if output_cost_per_million is not None
            else None
        )
        if (self.input_cost_per_million is None) != (self.output_cost_per_million is None):
            raise ValueError("input/output pricing must be supplied together")
        self._opener = opener or _urlopen_without_redirects

    @property
    def endpoint(self) -> str:
        return chat_completions_url(self.base_url)

    @staticmethod
    def _usage_int(usage: Mapping[str, Any], *names: str) -> int:
        for name in names:
            value = usage.get(name)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    return 0
        return 0

    def _cost(self, usage: Mapping[str, Any], input_tokens: int, output_tokens: int) -> float | None:
        for key in ("total_cost_usd", "cost_usd", "cost"):
            value = usage.get(key)
            if value is not None:
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    parsed = math.nan
                if math.isfinite(parsed) and parsed >= 0:
                    return parsed
        if self.input_cost_per_million is None or self.output_cost_per_million is None:
            return None
        return (input_tokens * self.input_cost_per_million + output_tokens * self.output_cost_per_million) / 1_000_000

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        response_format: Mapping[str, Any] | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> ChatCompletionResult:
        if not messages:
            raise ValueError("messages must not be empty")
        body: dict[str, Any] = {"model": self.model, "messages": [dict(message) for message in messages]}
        if response_format is not None:
            body["response_format"] = dict(response_format)
        if extra_body:
            body.update(extra_body)
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError(f"Chat Completions request exceeded {self.timeout_seconds:g}s") from exc
        except urllib.error.HTTPError as exc:
            raise ChatCompletionError(f"Chat Completions HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ChatCompletionError("Chat Completions connection failed") from exc
        except OSError as exc:
            raise ChatCompletionError(
                f"Chat Completions request failed: {redact_diagnostic(exc, secrets=(self.api_key,))}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatCompletionError("Chat Completions returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ChatCompletionError("Chat Completions response must be a JSON object")
        if isinstance(payload.get("error"), Mapping):
            message = redact_diagnostic(
                payload["error"].get("message", "unknown provider error"), secrets=(self.api_key,)
            )
            raise ChatCompletionError(f"Chat Completions provider error: {message}")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ChatCompletionError("Chat Completions response contained no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise ChatCompletionError("Chat Completions response contained no message")
        text = _content_text(message.get("content"))
        if not text:
            raise ChatCompletionError("Chat Completions response contained empty message content")
        usage = payload.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        input_tokens = self._usage_int(usage_map, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_int(usage_map, "completion_tokens", "output_tokens")
        return ChatCompletionResult(
            final_text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._cost(usage_map, input_tokens, output_tokens),
            response=payload,
        )


__all__ = ["ChatCompletionError", "ChatCompletionResult", "OpenAIChatCompletionsClient"]
