"""Shared helpers for the plugin-owned native Omni engines.

The published ``gepa`` package intentionally remains outside this module.  The
native engines use the small :mod:`native_omni.core` and
:mod:`native_omni.runners` contracts instead, so they can run with the PyPI
package (or with no GEPA installation at all).
"""

from __future__ import annotations

import inspect
import json
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import BudgetExhausted, EvalServer, Result, Task
from .runners import (
    AgentRunner,
    AgentRunResult,
    OpenAIChatCompletionRunner,
    TokenBudgetExceeded,
)


def safe_name(value: str, *, fallback: str = "item") -> str:
    """Return a stable filesystem-safe name without permitting separators."""

    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    return cleaned.strip(".") or fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def workspace_context(
    work_dir: Path,
    relative_paths: Sequence[str | Path],
    *,
    max_chars: int = 120_000,
) -> str:
    """Render selected materialized workspace files as prompt data."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    sections: list[str] = []
    remaining = max_chars
    for relative_path in relative_paths:
        relative = Path(relative_path)
        path = work_dir / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content:
            continue
        if len(content) > remaining:
            content = content[:remaining] + "\n[workspace context truncated]"
        sections.append(f"--- {relative.as_posix()} ---\n{content}")
        remaining -= len(content)
        if remaining <= 0:
            break
    return "\n\n".join(sections) or "(No materialized workspace files were found.)"


def call_factory(factory: Callable[..., Any], **kwargs: Any) -> Any:
    """Call test/user supplied factories while retaining narrow old hooks."""

    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return factory(**kwargs)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return factory(**kwargs)
    return factory(**{key: value for key, value in kwargs.items() if key in parameters})


def make_runner(
    backend: str,
    *,
    model: str | None,
    timeout_seconds: float,
    codex_command: str,
    pi_command: str,
    claude_command: str,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    work_dir: Path,
    sandbox: bool,
    factory: Callable[..., AgentRunner] | None = None,
) -> AgentRunner:
    """Construct one configured native runner.

    A factory is intentionally available for focused tests and embedding
    applications; production callers still receive one of the reviewed native
    runner classes.
    """

    if sandbox is not True:
        raise ValueError("sandbox must be True; native agent execution is sandboxed")
    normalized = str(backend).strip().lower()
    if normalized not in {"codex", "pi", "claude"}:
        raise ValueError("agent_backend must be one of: codex, pi, claude")
    kwargs: dict[str, Any] = {
        "model": model,
        "timeout_seconds": timeout_seconds,
        "work_dir": work_dir,
        "sandbox": sandbox,
        "command": {
            "codex": codex_command,
            "pi": pi_command,
            "claude": claude_command,
        }[normalized],
    }
    kwargs.update(
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )
    if factory is not None:
        # ``backend`` and all options are useful to custom factories; narrow
        # test hooks can simply accept the subset they understand.
        return call_factory(factory, backend=normalized, **kwargs)
    return OpenAIChatCompletionRunner(
        backend=normalized,
        model=model,
        timeout_seconds=timeout_seconds,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )


def task_examples(task: Task, server: EvalServer) -> tuple[str, list[tuple[str, Any]]]:
    """Return the agent-visible optimization split and stable examples."""

    if task.dataset:
        return "train", list(server.iter_split("train"))
    if task.valset:
        return "val", list(server.iter_split("val"))
    return "single", []


def score_candidate(server: EvalServer, candidate: str) -> tuple[float, dict[str, Any]]:
    """Evaluate one candidate on the optimization pool."""

    split, examples = task_examples(server.task, server)
    if split == "single" or not examples:
        return server.evaluate(candidate)
    return server.evaluate_split(candidate, split)


def score_candidate_safely(server: EvalServer, candidate: str) -> tuple[float, dict[str, Any]] | None:
    try:
        return score_candidate(server, candidate)
    except BudgetExhausted:
        return None


def heldout_metadata(server: EvalServer, candidate: str) -> dict[str, Any]:
    """Score the sealed test set without touching the optimization budget."""

    heldout = server.score_heldout(candidate)
    if heldout is None:
        return {}
    score, info = heldout
    scores = info.get("scores", [])
    return {
        "heldout_score": score,
        "heldout_scores": scores,
        "heldout_num_evaluated": info.get("num_evaluated", 0),
        # Keep the public wrapper's established metadata names as aliases;
        # the heldout_* names remain useful for native-engine diagnostics.
        "test_score": score,
        "test_scores": scores,
    }


def finalize_result(
    result: Result,
    *,
    server: EvalServer,
    output_dir: Path,
    engine: str,
    extra_metadata: Mapping[str, Any] | None = None,
) -> Result:
    """Attach common metadata, persist the result, and return it."""

    result.metadata.update(
        {"engine": engine, "output_dir": str(output_dir), **heldout_metadata(server, result.best_candidate)}
    )
    if extra_metadata:
        result.metadata.update(extra_metadata)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.persist(output_dir)
    return result


def result_from_candidates(
    server: EvalServer,
    candidates: Mapping[str, tuple[str, float, Mapping[str, Any]]],
    *,
    fallback: str,
    metadata: Mapping[str, Any] | None = None,
) -> Result:
    """Build a result from aggregate scores already returned by the server."""

    if not candidates:
        return Result(fallback, 0.0, server.budget.used, server.eval_log, dict(metadata or {}))
    name, (candidate, score, info) = max(candidates.items(), key=lambda item: item[1][1])
    merged = {"winner": name, **dict(info), **dict(metadata or {})}
    return Result(candidate, float(score), server.budget.used, server.eval_log, merged)


def extract_cost(result: AgentRunResult) -> float | None:
    value = result.cost_usd
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def new_session_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "AgentRunResult",
    "BudgetExhausted",
    "TokenBudgetExceeded",
    "call_factory",
    "extract_cost",
    "finalize_result",
    "heldout_metadata",
    "make_runner",
    "new_session_id",
    "result_from_candidates",
    "safe_name",
    "score_candidate",
    "score_candidate_safely",
    "task_examples",
    "workspace_context",
    "write_json",
]
