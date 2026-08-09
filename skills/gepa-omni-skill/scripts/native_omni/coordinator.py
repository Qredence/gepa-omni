"""Coordinator for native standalone engines and the Omni portfolio."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime_guards import require_sandbox, validate_external_path

from .autoresearch import AutoResearchConfig, AutoResearchEngine
from .best_of_n import BestOfNConfig, BestOfNEngine
from .common import finalize_result
from .core import BudgetTracker, EvalServer, Result, Task
from .meta_harness import MetaHarnessConfig, MetaHarnessEngine


NATIVE_ENGINES = ("autoresearch", "meta_harness", "best_of_n")
EXPLORATION_ENGINES = ("gepa", "autoresearch", "meta_harness")


@dataclass(frozen=True)
class NativeBudget:
    max_evals: int | None
    max_token_cost: float | None


def _validate_budget(
    max_evals: int | None,
    max_token_cost: float | None,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> None:
    if max_evals is None and max_token_cost is None:
        raise ValueError("native optimization requires max_evals or max_token_cost")
    if max_evals is not None and (isinstance(max_evals, bool) or not isinstance(max_evals, int) or max_evals <= 0):
        raise ValueError("max_evals must be a positive integer")
    if max_token_cost is not None and (
        isinstance(max_token_cost, bool)
        or not isinstance(max_token_cost, (int, float))
        or not math.isfinite(max_token_cost)
        or max_token_cost <= 0
    ):
        raise ValueError("max_token_cost must be a positive finite number")
    rates = (input_cost_per_million, output_cost_per_million)
    if any(
        rate is not None
        and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(float(rate)) or rate < 0)
        for rate in rates
    ):
        raise ValueError("native Chat Completions pricing must be finite non-negative numbers")
    if (input_cost_per_million is None) != (output_cost_per_million is None):
        raise ValueError("native Chat Completions input/output pricing must be supplied together")
    if max_token_cost is not None and input_cost_per_million is None:
        raise ValueError("native max_token_cost requires both input/output pricing rates")


def _phase_task(task: Mapping[str, Any], seed_candidate: str, *, include_test_set: bool) -> dict[str, Any]:
    value = dict(task)
    value.pop("config", None)
    value["seed_candidate"] = seed_candidate
    if not include_test_set:
        value.pop("test_set", None)
    return value


def _engine(
    engine: str,
    *,
    budget: NativeBudget,
    run_dir: Path,
    stop_at_score: float | None,
    sandbox: bool,
    agent_backend: str,
    agent_model: str | None,
    codex_command: str,
    pi_command: str,
    claude_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    runner_factory: Callable[..., Any] | None,
    max_n: int | None,
    max_iterations: int | None,
    max_candidates_per_iter: int,
    ralph: bool,
) -> Any:
    common = {
        "model": agent_model,
        "agent_backend": agent_backend,
        "codex_command": codex_command,
        "pi_command": pi_command,
        "claude_command": claude_command,
        "codex_input_cost_per_million": codex_input_cost_per_million,
        "codex_output_cost_per_million": codex_output_cost_per_million,
        "timeout_seconds": codex_timeout_seconds,
        "max_token_cost": budget.max_token_cost,
        "sandbox": sandbox,
        "run_dir": run_dir,
        "runner_factory": runner_factory,
        "stop_at_score": stop_at_score,
    }
    if engine == "autoresearch":
        return AutoResearchEngine(
            AutoResearchConfig(
                **common,
                ralph=ralph,
                max_iterations=max_iterations,
            )
        )
    if engine == "meta_harness":
        return MetaHarnessEngine(
            MetaHarnessConfig(
                **common,
                max_iterations=max_iterations,
                max_candidates_per_iter=max_candidates_per_iter,
            )
        )
    if engine == "best_of_n":
        return BestOfNEngine(BestOfNConfig(**common, max_n=max_n))
    raise ValueError(f"unsupported native engine {engine!r}; choose " + ", ".join(NATIVE_ENGINES))


def run_native_engine(
    seed_candidate: str,
    *,
    engine: str,
    task: Mapping[str, Any],
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    stop_at_score: float | None = None,
    sandbox: bool = True,
    agent_backend: str = "codex",
    agent_model: str | None = None,
    codex_command: str = "codex",
    pi_command: str = "pi",
    claude_command: str = "claude",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    runner_factory: Callable[..., Any] | None = None,
    max_n: int | None = None,
    max_iterations: int | None = None,
    max_candidates_per_iter: int = 3,
    ralph: bool = True,
) -> Result:
    """Run one native engine with an isolated budget and external artifacts."""

    require_sandbox(sandbox)
    if engine not in NATIVE_ENGINES:
        raise ValueError(f"unsupported native engine {engine!r}; choose " + ", ".join(NATIVE_ENGINES))
    if not isinstance(seed_candidate, str):
        raise TypeError("native engines accept only a string seed_candidate")
    _validate_budget(
        max_evals,
        max_token_cost,
        codex_input_cost_per_million,
        codex_output_cost_per_million,
    )
    run_root = validate_external_path(run_dir, label="run_dir")
    output_root = validate_external_path(output_dir, label="output_dir")
    task_obj = Task.from_mapping(task, seed_candidate=seed_candidate)
    budget = NativeBudget(max_evals, max_token_cost)
    tracker = BudgetTracker(max_evals=max_evals)
    server = EvalServer(task_obj, tracker, output_dir=output_root)
    engine_impl = _engine(
        engine,
        budget=budget,
        run_dir=run_root,
        stop_at_score=stop_at_score,
        sandbox=sandbox,
        agent_backend=agent_backend,
        agent_model=agent_model,
        codex_command=codex_command,
        pi_command=pi_command,
        claude_command=claude_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        runner_factory=runner_factory,
        max_n=max_n,
        max_iterations=max_iterations,
        max_candidates_per_iter=max_candidates_per_iter,
        ralph=ralph,
    )
    try:
        with server:
            result = engine_impl.run(task_obj, server)
            return finalize_result(result, server=server, output_dir=output_root, engine=engine)
    finally:
        server.close()


def _choose_result(results: list[tuple[str, Result]]) -> tuple[str, Result]:
    if not results:
        raise RuntimeError("native Omni produced no branch results")
    return max(results, key=lambda item: item[1].best_score)


def run_native_omni(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    slices: tuple[Any, ...],
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str = "autoresearch",
    stop_at_score: float | None = None,
    sandbox: bool = True,
    agent_backend: str = "codex",
    agent_model: str | None = None,
    codex_command: str = "codex",
    pi_command: str = "pi",
    claude_command: str = "claude",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    runner_factory: Callable[..., Any] | None = None,
    max_n: int | None = None,
    max_iterations: int | None = None,
    max_candidates_per_iter: int = 3,
    ralph: bool = True,
    gepa_continuation: Callable[..., Result] | None = None,
) -> Result:
    """Run the PyPI GEPA/AutoResearch/Meta-Harness portfolio and continue fresh."""

    if len(slices) != 4:
        raise ValueError("native Omni requires three branch slices and one continuation slice")
    if continuation_engine != "gepa" and continuation_engine not in NATIVE_ENGINES:
        raise ValueError("continuation_engine must be one of: gepa, " + ", ".join(NATIVE_ENGINES))
    if gepa_continuation is None:
        raise ValueError("native Omni requires the explicit PyPI GEPA callback for its reflective branch")
    root = validate_external_path(run_dir, label="run_dir")
    output_root = validate_external_path(output_dir, label="output_dir")
    branches: list[tuple[str, Result]] = []
    branch_task = _phase_task(task, seed_candidate, include_test_set=False)
    for index, engine in enumerate(EXPLORATION_ENGINES):
        budget = slices[index]
        if engine == "gepa":
            assert gepa_continuation is not None
            result = gepa_continuation(
                seed_candidate,
                task=branch_task,
                budget=budget,
                run_dir=root / "phase-1" / engine,
                output_dir=output_root / "phase-1" / engine,
            )
        else:
            result = run_native_engine(
                seed_candidate,
                engine=engine,
                task=branch_task,
                max_evals=budget.max_evals,
                max_token_cost=budget.max_token_cost,
                run_dir=root / "phase-1" / engine,
                output_dir=output_root / "phase-1" / engine,
                stop_at_score=stop_at_score,
                sandbox=sandbox,
                agent_backend=agent_backend,
                agent_model=agent_model,
                codex_command=codex_command,
                pi_command=pi_command,
                claude_command=claude_command,
                codex_input_cost_per_million=codex_input_cost_per_million,
                codex_output_cost_per_million=codex_output_cost_per_million,
                codex_timeout_seconds=codex_timeout_seconds,
                runner_factory=runner_factory,
                max_n=max_n,
                max_iterations=max_iterations,
                max_candidates_per_iter=max_candidates_per_iter,
                ralph=ralph,
            )
        branches.append((engine, result))
    winning_engine, winning_result = _choose_result(branches)
    continuation_budget = slices[3]
    continuation_task = _phase_task(task, winning_result.best_candidate, include_test_set=True)
    if continuation_engine == "gepa":
        assert gepa_continuation is not None
        final_result = gepa_continuation(
            winning_result.best_candidate,
            task=continuation_task,
            budget=continuation_budget,
            run_dir=root / "phase-2" / "gepa",
            output_dir=output_root / "phase-2" / "gepa",
        )
    else:
        final_result = run_native_engine(
            winning_result.best_candidate,
            engine=continuation_engine,
            task=continuation_task,
            max_evals=continuation_budget.max_evals,
            max_token_cost=continuation_budget.max_token_cost,
            run_dir=root / "phase-2" / continuation_engine,
            output_dir=output_root / "phase-2" / continuation_engine,
            stop_at_score=stop_at_score,
            sandbox=sandbox,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_command=codex_command,
            pi_command=pi_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            runner_factory=runner_factory,
            max_n=max_n,
            max_iterations=max_iterations,
            max_candidates_per_iter=max_candidates_per_iter,
            ralph=ralph,
        )
    final_result.metadata["omni"] = {
        "branches": [
            {"engine": engine, "best_score": result.best_score, "best_candidate": result.best_candidate}
            for engine, result in branches
        ],
        "winning_engine": winning_engine,
        "continuation_engine": continuation_engine,
        "budgets": [{"max_evals": item.max_evals, "max_token_cost": item.max_token_cost} for item in slices],
    }
    persist = getattr(final_result, "persist", None)
    if callable(persist):
        persist(output_root / "phase-2" / continuation_engine)
    return final_result


__all__ = ["EXPLORATION_ENGINES", "NATIVE_ENGINES", "NativeBudget", "run_native_engine", "run_native_omni"]
