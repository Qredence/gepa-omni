"""Internal two-phase Omni orchestration for the GEPA Omni skill.

The public API remains ``gepa.optimize_anything``. This module only composes
that API's existing ``optimize_best_of`` and ``optimize_anything`` entrypoints
for the Codex-native skill workflow.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

EXPLORATION_ENGINES = ("gepa", "autoresearch", "meta_harness")
STANDALONE_ENGINES = (*EXPLORATION_ENGINES, "best_of_n")
DEFAULT_CONTINUATION_ENGINE = "gepa"
AGENT_BACKENDS = ("codex", "pi", "claude")


class OmniBudgetError(ValueError):
    """Raised when a total budget cannot support four Omni phases."""


@dataclass(frozen=True)
class BudgetSlice:
    """Budget assigned to one optimizer stage."""

    max_evals: int | None
    max_token_cost: float | None


def _validate_budget(max_evals: int | None, max_token_cost: float | None) -> None:
    if max_evals is None and max_token_cost is None:
        raise OmniBudgetError(
            "Omni requires an explicit max_evals or max_token_cost budget; "
            "use a standalone engine for an unbounded run."
        )
    if max_evals is not None and (
        isinstance(max_evals, bool) or not isinstance(max_evals, int) or max_evals <= 0
    ):
        raise OmniBudgetError("max_evals must be a positive integer")
    if max_token_cost is not None and (
        isinstance(max_token_cost, bool)
        or not isinstance(max_token_cost, (int, float))
        or not math.isfinite(max_token_cost)
        or max_token_cost <= 0
    ):
        raise OmniBudgetError("max_token_cost must be a positive finite number")


def _split_evals(total: int | None) -> tuple[int | None, ...]:
    if total is None:
        return (None, None, None, None)
    quotient, remainder = divmod(total, 4)
    if quotient == 0:
        raise OmniBudgetError(
            "Omni requires at least four max_evals so each of the three "
            "exploration engines and the continuation gets a positive slice"
        )
    return (quotient, quotient, quotient, quotient + remainder)


def _split_tokens(total: float | None) -> tuple[float | None, ...]:
    if total is None:
        return (None, None, None, None)
    slice_cost = total / 4.0
    return (slice_cost, slice_cost, slice_cost, slice_cost)


def partition_budget(
    max_evals: int | None, max_token_cost: float | None
) -> tuple[BudgetSlice, ...]:
    """Partition one total budget into three exploration and one continuation slice."""

    _validate_budget(max_evals, max_token_cost)
    return tuple(
        BudgetSlice(evals, tokens)
        for evals, tokens in zip(_split_evals(max_evals), _split_tokens(max_token_cost))
    )


def _full_budget(max_evals: int | None, max_token_cost: float | None) -> BudgetSlice:
    _validate_budget(max_evals, max_token_cost)
    return BudgetSlice(max_evals, max_token_cost)


def _load_launcher(launcher: Any | None, config_cls: Callable[..., Any] | None) -> tuple[Any, Any]:
    if launcher is not None:
        if config_cls is None:
            config_cls = getattr(launcher, "OptimizeAnythingConfig", None)
        if config_cls is None:
            raise TypeError(
                "config_cls is required when launcher does not expose "
                "OptimizeAnythingConfig"
            )
        return launcher, config_cls
    from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything, optimize_best_of

    return (
        launcher
        or SimpleNamespace(
            optimize_anything=optimize_anything,
            optimize_best_of=optimize_best_of,
        ),
        config_cls or OptimizeAnythingConfig,
    )


def _make_codex_proposer(
    run_dir: Path,
    *,
    model: str | None,
    timeout_seconds: float,
    factory: Callable[..., Any] | None,
) -> Any:
    if factory is None:
        from codex_agent_proposer import CodexAgentProposer

        factory = CodexAgentProposer
    return factory(run_dir=run_dir, model=model, timeout_seconds=timeout_seconds)


def _engine_config(
    engine: str,
    *,
    run_dir: Path,
    agent_backend: str,
    agent_model: str | None,
    codex_model: str | None,
    pi_command: str,
    codex_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    codex_proposer_factory: Callable[..., Any] | None,
) -> dict[str, Any]:
    if agent_backend not in AGENT_BACKENDS:
        raise ValueError(
            f"unsupported agent_backend {agent_backend!r}; choose "
            + ", ".join(AGENT_BACKENDS)
        )
    if engine == "gepa":
        proposer = _make_codex_proposer(
            run_dir / "proposer",
            model=codex_model,
            timeout_seconds=codex_timeout_seconds,
            factory=codex_proposer_factory,
        )
        return {
            "engine": {"max_workers": 1, "parallel": False},
            "reflection": {
                "reflection_lm": None,
                "custom_candidate_proposer": proposer,
                "module_selector": "all",
            },
        }
    if engine == "autoresearch":
        model = codex_model if agent_backend == "codex" and codex_model is not None else agent_model
        if agent_backend == "claude" and model is None:
            model = "claude-sonnet-4-6"
        config: dict[str, Any] = {
            "agent_backend": agent_backend,
            "model": model,
            "ralph": True,
            "max_no_eval_seconds": 300,
        }
        if agent_backend == "pi":
            config["pi_command"] = pi_command
        elif agent_backend == "codex":
            config.update(
                {
                    "codex_command": codex_command,
                    "codex_input_cost_per_million": codex_input_cost_per_million,
                    "codex_output_cost_per_million": codex_output_cost_per_million,
                }
            )
        return config
    if engine == "meta_harness":
        model = codex_model if agent_backend == "codex" and codex_model is not None else agent_model
        if agent_backend == "claude" and model is None:
            model = "claude-sonnet-4-6"
        config = {
            "agent_backend": agent_backend,
            "model": model,
            "max_iterations": 20,
            "max_candidates_per_iter": 3,
        }
        if agent_backend == "pi":
            config["pi_command"] = pi_command
        elif agent_backend == "codex":
            config.update(
                {
                    "codex_command": codex_command,
                    "codex_input_cost_per_million": codex_input_cost_per_million,
                    "codex_output_cost_per_million": codex_output_cost_per_million,
                    "timeout_seconds": codex_timeout_seconds,
                }
            )
        return config
    if engine == "best_of_n":
        return {"model": agent_model} if agent_model is not None else {}
    raise ValueError(f"unsupported optimizer engine: {engine}")


def _make_config(
    config_cls: Callable[..., Any],
    engine: str,
    budget: BudgetSlice,
    *,
    run_dir: Path,
    output_dir: Path,
    stop_at_score: float | None,
    max_concurrency: int,
    sandbox: bool,
    agent_backend: str,
    agent_model: str | None,
    codex_model: str | None,
    pi_command: str,
    codex_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    codex_proposer_factory: Callable[..., Any] | None,
) -> Any:
    kwargs: dict[str, Any] = {
        "engine": engine,
        "max_concurrency": max_concurrency,
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "sandbox": sandbox,
        "engine_config": _engine_config(
            engine,
            run_dir=run_dir,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_command=pi_command,
            codex_command=codex_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_proposer_factory=codex_proposer_factory,
        ),
    }
    if budget.max_evals is not None:
        kwargs["max_evals"] = budget.max_evals
    if budget.max_token_cost is not None:
        kwargs["max_token_cost"] = budget.max_token_cost
    if stop_at_score is not None:
        kwargs["stop_at_score"] = stop_at_score
    return config_cls(**kwargs)


def _task_for_phase(task: Mapping[str, Any], *, include_test_set: bool) -> dict[str, Any]:
    phase_task = dict(task)
    phase_task.pop("config", None)
    if not include_test_set:
        phase_task.pop("test_set", None)
    return phase_task


def _annotate_result(result: Any, *, continuation_engine: str, slices: tuple[BudgetSlice, ...]) -> None:
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["omni"] = {
        "exploration_engines": list(EXPLORATION_ENGINES),
        "continuation_engine": continuation_engine,
        "exploration_budget": asdict(slices[0]),
        "continuation_budget": asdict(slices[3]),
    }


def run_omni(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str = DEFAULT_CONTINUATION_ENGINE,
    stop_at_score: float | None = None,
    max_concurrency: int = 1,
    sandbox: bool = True,
    agent_backend: str = "codex",
    agent_model: str | None = None,
    codex_model: str | None = None,
    pi_command: str = "pi",
    codex_command: str = "codex",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    launcher: Any | None = None,
    config_cls: Callable[..., Any] | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
) -> Any:
    """Run Phase 1 exploration followed by a fresh Phase 2 continuation."""

    if not isinstance(seed_candidate, str):
        raise TypeError("seed_candidate must be a string")
    if continuation_engine not in EXPLORATION_ENGINES:
        raise ValueError(
            "continuation_engine must be one of: "
            + ", ".join(EXPLORATION_ENGINES)
        )
    slices = partition_budget(max_evals, max_token_cost)
    launcher, config_cls = _load_launcher(launcher, config_cls)
    root = Path(run_dir)
    output_root = Path(output_dir)
    exploration_configs = [
        _make_config(
            config_cls,
            engine,
            slices[index],
            run_dir=root / "phase-1" / engine,
            output_dir=output_root / "phase-1" / engine,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_command=pi_command,
            codex_command=codex_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_proposer_factory=codex_proposer_factory,
        )
        for index, engine in enumerate(EXPLORATION_ENGINES)
    ]
    exploration = launcher.optimize_best_of(
        seed_candidate,
        **_task_for_phase(task, include_test_set=False),
        configs=exploration_configs,
    )
    best_candidate = getattr(exploration, "best_candidate", None)
    if not isinstance(best_candidate, str):
        raise TypeError("Omni exploration returned no string best_candidate")
    continuation_config = _make_config(
        config_cls,
        continuation_engine,
        slices[3],
        run_dir=root / "phase-2" / continuation_engine,
        output_dir=output_root / "phase-2" / continuation_engine,
        stop_at_score=stop_at_score,
        max_concurrency=max_concurrency,
        sandbox=sandbox,
        agent_backend=agent_backend,
        agent_model=agent_model,
        codex_model=codex_model,
        pi_command=pi_command,
        codex_command=codex_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        codex_proposer_factory=codex_proposer_factory,
    )
    result = launcher.optimize_anything(
        best_candidate,
        **_task_for_phase(task, include_test_set=True),
        config=continuation_config,
    )
    _annotate_result(result, continuation_engine=continuation_engine, slices=slices)
    return result


def run_optimization(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    engine: str | None = None,
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str = DEFAULT_CONTINUATION_ENGINE,
    stop_at_score: float | None = None,
    max_concurrency: int = 1,
    sandbox: bool = True,
    agent_backend: str = "codex",
    agent_model: str | None = None,
    codex_model: str | None = None,
    pi_command: str = "pi",
    codex_command: str = "codex",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    launcher: Any | None = None,
    config_cls: Callable[..., Any] | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
) -> Any:
    """Run default Omni orchestration or one explicitly selected engine."""

    if not isinstance(seed_candidate, str):
        raise TypeError("seed_candidate must be a string")
    if engine is None:
        return run_omni(
            seed_candidate,
            task=task,
            max_evals=max_evals,
            max_token_cost=max_token_cost,
            run_dir=run_dir,
            output_dir=output_dir,
            continuation_engine=continuation_engine,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_command=pi_command,
            codex_command=codex_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            launcher=launcher,
            config_cls=config_cls,
            codex_proposer_factory=codex_proposer_factory,
        )
    if engine not in STANDALONE_ENGINES:
        raise ValueError(
            f"unsupported standalone engine {engine!r}; omit engine for Omni or use "
            + ", ".join(STANDALONE_ENGINES)
        )
    launcher, config_cls = _load_launcher(launcher, config_cls)
    budget = _full_budget(max_evals, max_token_cost)
    stage_dir = Path(run_dir) / "standalone" / engine
    config = _make_config(
        config_cls,
        engine,
        budget,
        run_dir=stage_dir,
        output_dir=Path(output_dir) / "standalone" / engine,
        stop_at_score=stop_at_score,
        max_concurrency=max_concurrency,
        sandbox=sandbox,
        agent_backend=agent_backend,
        agent_model=agent_model,
        codex_model=codex_model,
        pi_command=pi_command,
        codex_command=codex_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        codex_proposer_factory=codex_proposer_factory,
    )
    return launcher.optimize_anything(
        seed_candidate,
        **_task_for_phase(task, include_test_set=True),
        config=config,
    )
