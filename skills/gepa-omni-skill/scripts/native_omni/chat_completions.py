"""Small OpenAI-compatible Chat Completions client used by native engines.

The plugin deliberately talks to a Chat Completions compatible HTTP endpoint
instead of depending on a provider-specific CLI.  Production configuration is
read from ``OPENAI_BASE_URL``, ``OPENAI_MODEL``, and ``OPENAI_API_KEY``.  The
constructor accepts explicit values only to make embedding and unit tests
deterministic; environment values take precedence when present.
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


class ChatCompletionError(RuntimeError):
    """Raised when an OpenAI-compatible Chat Completions request fails."""


Urlopen = Callable[..., Any]


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
        timeout_seconds: float = 600.0,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        opener: Urlopen | None = None,
    ) -> None:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        self.base_url = os.environ.get("OPENAI_BASE_URL") or base_url
        self.model = os.environ.get("OPENAI_MODEL") or model
        self.api_key = os.environ.get("OPENAI_API_KEY") or api_key
        missing = [
            name
            for name, value in (
                ("OPENAI_BASE_URL", self.base_url),
                ("OPENAI_MODEL", self.model),
                ("OPENAI_API_KEY", self.api_key),
            )
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise ValueError("missing required Chat Completions configuration: " + ", ".join(missing))
        self.base_url = self.base_url.strip().rstrip("/")
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
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
        self._opener = opener or urllib.request.urlopen

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

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
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ChatCompletionError(f"Chat Completions HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ChatCompletionError(f"Chat Completions connection failed: {exc.reason}") from exc
        except OSError as exc:
            raise ChatCompletionError(f"Chat Completions request failed: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatCompletionError("Chat Completions returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ChatCompletionError("Chat Completions response must be a JSON object")
        if isinstance(payload.get("error"), Mapping):
            message = payload["error"].get("message", "unknown provider error")
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
