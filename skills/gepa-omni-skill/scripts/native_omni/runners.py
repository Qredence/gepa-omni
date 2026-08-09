"""OpenAI-compatible Chat Completions runners for native Omni engines.

Adapted from the MIT-licensed GEPA project at commit
8a2bed96385202f69caaeb5327a843ed2f5ea225. The adaptation retains the public
runner result and budget contracts while replacing provider subprocesses with
the plugin-owned Chat Completions client.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .chat_completions import OpenAIChatCompletionsClient


class AgentProcessError(RuntimeError):
    """Raised when a Chat Completions request cannot produce a response."""


class AgentTimeout(TimeoutError):
    """Raised when a Chat Completions request exceeds its timeout."""


class TokenBudgetExceeded(RuntimeError):
    """Raised when reported proposer cost exceeds the caller's dollar budget."""


@dataclass(frozen=True)
class AgentRunResult:
    final_text: str
    session_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    returncode: int
    command: tuple[str, ...]
    stdout: str
    stderr: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        work_dir: str | Path,
        session_id: str | None = None,
        max_token_cost: float | None = None,
        spent_token_cost: float = 0.0,
    ) -> AgentRunResult: ...


def _runtime_checkout() -> Path:
    return Path(__file__).resolve().parents[4]


def _external_work_dir(work_dir: str | Path) -> Path:
    path = Path(work_dir)
    if not path.is_absolute():
        raise ValueError("work_dir must be an absolute external path")
    resolved = path.resolve()
    try:
        resolved.relative_to(_runtime_checkout())
    except ValueError:
        pass
    else:
        raise ValueError("work_dir must be outside the plugin checkout")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _validate_token_budget(
    max_token_cost: float | None,
    spent_token_cost: float,
) -> tuple[float | None, float]:
    """Validate a shared dollar-budget contract before making an API call."""
    if max_token_cost is not None:
        if isinstance(max_token_cost, bool) or not isinstance(max_token_cost, (int, float)):
            raise ValueError("max_token_cost must be a finite non-negative number or None")
        max_token_cost = float(max_token_cost)
        if not math.isfinite(max_token_cost) or max_token_cost < 0:
            raise ValueError("max_token_cost must be a finite non-negative number or None")
    if isinstance(spent_token_cost, bool) or not isinstance(spent_token_cost, (int, float)):
        raise ValueError("spent_token_cost must be a finite non-negative number")
    spent_token_cost = float(spent_token_cost)
    if not math.isfinite(spent_token_cost) or spent_token_cost < 0:
        raise ValueError("spent_token_cost must be a finite non-negative number")
    if max_token_cost is not None and spent_token_cost >= max_token_cost:
        raise TokenBudgetExceeded("no token budget remains")
    return max_token_cost, spent_token_cost


class OpenAIChatCompletionRunner:
    """Run native agent turns through an OpenAI-compatible API.

    Chat Completions is stateless at the HTTP layer, so one runner instance
    retains its message history for AutoResearch continuation turns. The
    Meta-Harness and Best-of-N engines construct a new runner for each fresh
    session, keeping their contexts independent.
    """

    def __init__(
        self,
        *,
        backend: str = "codex",
        model: str | None = None,
        timeout_seconds: float = 600,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        self.backend = str(backend).strip().lower() or "codex"
        self.client = OpenAIChatCompletionsClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            opener=opener,
        )
        self.timeout_seconds = timeout
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self._messages: list[dict[str, Any]] = []
        self._session_id: str | None = None

    def run(
        self,
        prompt: str,
        *,
        work_dir: str | Path,
        session_id: str | None = None,
        max_token_cost: float | None = None,
        spent_token_cost: float = 0.0,
    ) -> AgentRunResult:
        max_token_cost, spent_token_cost = _validate_token_budget(max_token_cost, spent_token_cost)
        if max_token_cost is not None and (
            self.client.input_cost_per_million is None or self.client.output_cost_per_million is None
        ):
            raise ValueError("Chat Completions max_token_cost requires both input/output pricing rates")
        directory = _external_work_dir(work_dir)
        requested_session = session_id or self._session_id or str(uuid.uuid4())
        if self._session_id is not None and session_id is not None and session_id != self._session_id:
            self._messages = []
        messages = [*self._messages, {"role": "user", "content": prompt}]
        try:
            response = self.client.complete(messages)
        except TimeoutError as exc:
            raise AgentTimeout(str(exc)) from exc
        except Exception as exc:
            raise AgentProcessError(f"OpenAI Chat Completions request failed: {exc}") from exc

        self._messages.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.final_text},
            ]
        )
        self._session_id = requested_session
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        if response.cost_usd is not None:
            self.total_cost_usd += response.cost_usd
        if max_token_cost is not None:
            if response.cost_usd is None:
                raise TokenBudgetExceeded("Chat Completions did not report cost for a cost-bounded invocation")
            if spent_token_cost + response.cost_usd > max_token_cost:
                raise TokenBudgetExceeded("Chat Completions token cost exceeded max_token_cost")

        response_json = json.dumps(response.response, ensure_ascii=False, indent=2, default=str) + "\n"
        (directory / "chat_completion_response.json").write_text(response_json, encoding="utf-8")
        return AgentRunResult(
            final_text=response.final_text,
            session_id=requested_session,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            returncode=0,
            command=("openai-chat-completions", self.client.endpoint),
            stdout=json.dumps(response.response, ensure_ascii=False),
            stderr="",
            metadata={"backend": self.backend, "model": self.client.model, "session_mode": "chat_history"},
        )

    def close(self) -> None:
        """Release hook matching the historical runner contract."""


class CodexAgentRunner(OpenAIChatCompletionRunner):
    """Compatibility name that now uses Chat Completions instead of a CLI."""

    def __init__(
        self,
        command: str = "codex",
        *,
        model: str | None = None,
        timeout_seconds: float = 600,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        **kwargs: Any,
    ) -> None:
        del command
        super().__init__(
            backend="codex",
            model=model,
            timeout_seconds=timeout_seconds,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            **kwargs,
        )


class PiAgentRunner(OpenAIChatCompletionRunner):
    """Compatibility name that now uses Chat Completions instead of a CLI."""

    def __init__(
        self,
        command: str = "pi",
        *,
        model: str | None = None,
        timeout_seconds: float = 600,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        sandbox_prefix: Any = None,
        **kwargs: Any,
    ) -> None:
        del command, sandbox_prefix
        super().__init__(
            backend="pi",
            model=model,
            timeout_seconds=timeout_seconds,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            **kwargs,
        )


class ClaudeAgentRunner(OpenAIChatCompletionRunner):
    """Compatibility name that now uses Chat Completions instead of a CLI."""

    def __init__(
        self,
        command: str = "claude",
        *,
        model: str | None = None,
        timeout_seconds: float = 600,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        sandbox_prefix: Any = None,
        **kwargs: Any,
    ) -> None:
        del command, sandbox_prefix
        super().__init__(
            backend="claude",
            model=model,
            timeout_seconds=timeout_seconds,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            **kwargs,
        )


__all__ = [
    "AgentProcessError",
    "AgentRunResult",
    "AgentRunner",
    "AgentTimeout",
    "ClaudeAgentRunner",
    "CodexAgentRunner",
    "OpenAIChatCompletionRunner",
    "PiAgentRunner",
    "TokenBudgetExceeded",
]
