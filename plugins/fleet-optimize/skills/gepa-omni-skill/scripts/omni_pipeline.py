"""Internal two-phase Omni orchestration for the GEPA Omni skill.

The public API remains ``gepa.optimize_anything``. This module only composes
that API's existing ``optimize_best_of`` and ``optimize_anything`` entrypoints
for the Codex/Pi-native skill workflow.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
from inspect import Parameter, signature
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from runtime_guards import require_sandbox, validate_external_path
from gepa_omni.allocation import plan_auto_allocation

# The upstream package owns only the explicit ``engine="gepa"`` path.  The
# plugin-native portfolio is deliberately independent of that package.
EXPLORATION_ENGINES = ("gepa", "autoresearch", "meta_harness")
NATIVE_ENGINES = ("autoresearch", "meta_harness", "best_of_n")
STANDALONE_ENGINES = ("gepa", *NATIVE_ENGINES)
DEFAULT_CONTINUATION_ENGINE = "gepa"
# Kept solely for callers that inject the legacy launcher/config test seam.
_LEGACY_EXPLORATION_ENGINES = ("gepa", "autoresearch", "meta_harness")
_LEGACY_CONTINUATION_ENGINE = "gepa"
RUNNER_PROFILES = ("codex", "pi", "claude")
# Compatibility alias retained for callers of the pre-0.3 script API.
AGENT_BACKENDS = RUNNER_PROFILES


class OmniBudgetError(ValueError):
    """Raised when a total budget cannot support four Omni phases."""


def _resolve_runner_profile(runner_profile: str | None, agent_backend: str | None) -> str:
    """Resolve the renamed profile field while accepting one legacy alias."""

    profile = None if runner_profile is None else str(runner_profile).strip().lower()
    backend = None if agent_backend is None else str(agent_backend).strip().lower()
    if profile == "default":
        profile = "codex"
    if backend == "default":
        backend = "codex"
    if profile is not None and backend is not None and profile != backend:
        raise ValueError("agent_backend conflicts with runner_profile")
    selected = backend or profile or "codex"
    if selected not in RUNNER_PROFILES:
        raise ValueError(f"unsupported runner_profile {selected!r}; choose " + ", ".join(RUNNER_PROFILES))
    return selected


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
    if max_evals is not None and (isinstance(max_evals, bool) or not isinstance(max_evals, int) or max_evals <= 0):
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


def partition_budget(max_evals: int | None, max_token_cost: float | None) -> tuple[BudgetSlice, ...]:
    """Partition one total budget into three exploration and one continuation slice."""

    _validate_budget(max_evals, max_token_cost)
    return tuple(
        BudgetSlice(evals, tokens) for evals, tokens in zip(_split_evals(max_evals), _split_tokens(max_token_cost))
    )


def _full_budget(max_evals: int | None, max_token_cost: float | None) -> BudgetSlice:
    _validate_budget(max_evals, max_token_cost)
    return BudgetSlice(max_evals, max_token_cost)


def _validate_parallel_proposals(
    proposals: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if proposals is None:
        return None
    if not isinstance(proposals, tuple) or len(proposals) != 2:
        raise ValueError("gepa_parallel_proposals must be a (parents, mutations) tuple")
    parents, mutations = proposals
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in proposals):
        raise ValueError("gepa_parallel_proposals values must be positive integers")
    return parents, mutations


def _load_launcher(launcher: Any | None, config_cls: Callable[..., Any] | None) -> tuple[Any, Any]:
    if launcher is not None:
        if config_cls is None:
            config_cls = getattr(launcher, "OptimizeAnythingConfig", None) or getattr(launcher, "GEPAConfig", None)
        if config_cls is None:
            raise TypeError("config_cls is required when launcher does not expose a GEPA config class")
        return launcher, config_cls
    try:
        from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything, optimize_best_of

        return (
            launcher
            or SimpleNamespace(
                optimize_anything=optimize_anything,
                optimize_best_of=optimize_best_of,
                pypi_api=False,
            ),
            config_cls or OptimizeAnythingConfig,
        )
    except ImportError:
        from gepa.optimize_anything import GEPAConfig, optimize_anything

        return (
            launcher
            or SimpleNamespace(
                optimize_anything=optimize_anything,
                optimize_best_of=None,
                pypi_api=True,
            ),
            config_cls or GEPAConfig,
        )


def _is_pypi_config(config_cls: Callable[..., Any]) -> bool:
    return config_cls.__name__ in {"GEPAConfig", "OptimizeAnythingConfig"} and config_cls.__module__.startswith("gepa.")


def _validate_max_concurrency(max_concurrency: int) -> None:
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency <= 0:
        raise ValueError("max_concurrency must be a positive integer")


def _make_pypi_config(
    config_cls: Callable[..., Any],
    budget: BudgetSlice,
    *,
    run_dir: Path,
    stop_at_score: float | None,
    output_dir: Path | None = None,
    max_concurrency: int,
    agent_backend: str,
    agent_model: str | None,
    codex_model: str | None,
    codex_command: str,
    codex_timeout_seconds: float,
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
    gepa_parallel_proposals: tuple[int, int] | None = None,
) -> Any:
    if budget.max_token_cost is not None and (
        codex_input_cost_per_million is None or codex_output_cost_per_million is None
    ):
        raise ValueError(
            "Chat Completions input/output pricing rates are required when max_token_cost is set for PyPI GEPA"
        )

    proposer = _make_codex_proposer(
        run_dir / "proposer",
        model=codex_model if codex_model is not None else agent_model,
        timeout_seconds=codex_timeout_seconds,
        codex_command=codex_command,
        sandbox=True,
        input_cost_per_million=codex_input_cost_per_million,
        output_cost_per_million=codex_output_cost_per_million,
        max_token_cost=budget.max_token_cost,
        factory=codex_proposer_factory,
    )
    if config_cls.__name__ == "OptimizeAnythingConfig":
        engine_kwargs: dict[str, Any] = {
            "max_workers": max_concurrency,
            "parallel": gepa_parallel_proposals is not None,
            "run_dir": str(run_dir),
        }
        if gepa_parallel_proposals is not None:
            from gepa.strategies.proposal_sampling import PxNSampling
            from gepa.strategies.proposal_selection import AllImprovements

            parents, mutations = gepa_parallel_proposals
            engine_kwargs.update(
                {
                    "sampling_strategy": PxNSampling(p=parents, n=mutations),
                    "selection_strategy": AllImprovements(),
                }
            )
        return config_cls(
            engine="gepa",
            max_evals=budget.max_evals,
            max_token_cost=budget.max_token_cost,
            max_concurrency=max_concurrency,
            output_dir=str(output_dir if output_dir is not None else run_dir),
            run_dir=str(run_dir),
            stop_at_score=stop_at_score,
            engine_config={
                "engine": engine_kwargs,
                "reflection": {
                    "reflection_lm": None,
                    "custom_candidate_proposer": proposer,
                    "module_selector": "all",
                },
            },
        )
    from gepa.optimize_anything import EngineConfig, ReflectionConfig

    engine_kwargs: dict[str, Any] = {
        "max_metric_calls": budget.max_evals,
        "max_workers": max_concurrency,
        "parallel": gepa_parallel_proposals is not None,
        "run_dir": str(run_dir),
    }
    if gepa_parallel_proposals is not None:
        from gepa.strategies.proposal_sampling import PxNSampling
        from gepa.strategies.proposal_selection import AllImprovements

        parents, mutations = gepa_parallel_proposals
        engine_kwargs.update(
            {
                "sampling_strategy": PxNSampling(p=parents, n=mutations),
                "selection_strategy": AllImprovements(),
            }
        )
    stop_callbacks: list[Any] = []
    if stop_at_score is not None:
        from gepa.utils import ScoreThresholdStopper

        stop_callbacks.append(ScoreThresholdStopper(stop_at_score))
    if budget.max_token_cost is not None:
        from gepa.utils import MaxReflectionCostStopper

        stop_callbacks.append(MaxReflectionCostStopper(budget.max_token_cost, reflection_lm=proposer))
    return config_cls(
        engine=EngineConfig(**engine_kwargs),
        reflection=ReflectionConfig(
            reflection_lm=None,
            custom_candidate_proposer=proposer,
            module_selector="all",
        ),
        stop_callbacks=stop_callbacks or None,
    )


class _PublicResult:
    """Expose the plugin result shape while retaining the PyPI GEPA result."""

    def __init__(self, raw: Any, *, task: Mapping[str, Any], run_dir: Path, output_dir: Path) -> None:
        self.raw = raw
        candidate = raw.best_candidate
        if isinstance(candidate, Mapping) and set(candidate) == {"current_candidate"}:
            candidate = candidate["current_candidate"]
        self.best_candidate = candidate
        try:
            self.best_score = raw.best_score
        except AttributeError:
            self.best_score = raw.val_aggregate_scores[raw.best_idx]
        try:
            self.total_evals = raw.total_evals
        except AttributeError:
            self.total_evals = raw.total_metric_calls
        self.eval_log: list[Any] = []
        try:
            import gepa

            gepa_version = str(getattr(gepa, "__version__", "unknown"))
        except Exception:  # pragma: no cover - only reached when a fake launcher is injected
            gepa_version = "unknown"
        self.metadata: dict[str, Any] = {
            "engine": "gepa",
            "run_dir": str(run_dir),
            "output_dir": str(output_dir),
            "gepa_version": gepa_version,
        }
        scores = _score_public_test_set(candidate, task)
        if scores:
            self.metadata["test_score"] = sum(scores) / len(scores)
            self.metadata["test_scores"] = scores

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)


def _score_public_test_set(candidate: Any, task: Mapping[str, Any]) -> list[float] | None:
    """Score PyPI held-out examples without passing them into optimization."""

    test_set = task.get("test_set")
    if not test_set or not isinstance(candidate, str):
        return None

    evaluator = task.get("evaluator")
    if callable(evaluator):
        return [
            float(value[0] if isinstance(value := evaluator(candidate, example), tuple) else value)
            for example in test_set
        ]

    batch_evaluator = task.get("batch_evaluator")
    if not callable(batch_evaluator):
        return None
    pairs = [(candidate, example) for example in test_set]
    try:
        values = list(batch_evaluator(pairs))
    except TypeError as error:
        raise ValueError("batch_evaluator must return one result per test example") from error
    if len(values) != len(pairs):
        raise ValueError(
            "batch_evaluator must return exactly one result per test example "
            f"(expected {len(pairs)}, got {len(values)})"
        )
    return [float(value[0] if isinstance(value, tuple) else value) for value in values]


def _call_proposer_factory(factory: Callable[..., Any], **kwargs: Any) -> Any:
    """Pass new proposer options while retaining older narrow factory hooks."""

    try:
        factory_signature = signature(factory)
    except (TypeError, ValueError):
        return factory(**kwargs)
    parameters = factory_signature.parameters.values()
    if any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters):
        return factory(**kwargs)
    accepted = {name: value for name, value in kwargs.items() if name in factory_signature.parameters}
    return factory(**accepted)


def _make_codex_proposer(
    run_dir: Path,
    *,
    model: str | None,
    timeout_seconds: float,
    codex_command: str,
    sandbox: bool,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    max_token_cost: float | None = None,
    factory: Callable[..., Any] | None = None,
) -> Any:
    if factory is None:
        from codex_agent_proposer import CodexAgentProposer

        factory = CodexAgentProposer
    return _call_proposer_factory(
        factory,
        run_dir=run_dir,
        model=model,
        timeout_seconds=timeout_seconds,
        codex_command=codex_command,
        sandbox=sandbox,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        max_token_cost=max_token_cost,
    )


def _make_pi_proposer(
    run_dir: Path,
    *,
    model: str | None,
    timeout_seconds: float,
    pi_command: str,
    sandbox: bool,
    factory: Callable[..., Any] | None,
) -> Any:
    if factory is None:
        from pi_agent_proposer import PiAgentProposer

        factory = PiAgentProposer
    return _call_proposer_factory(
        factory,
        run_dir=run_dir,
        model=model,
        timeout_seconds=timeout_seconds,
        pi_command=pi_command,
        sandbox=sandbox,
    )


def _backend_model(
    backend: str,
    *,
    agent_model: str | None,
    codex_model: str | None,
    pi_model: str | None,
) -> str | None:
    if backend == "codex":
        return codex_model if codex_model is not None else agent_model
    if backend == "pi":
        return pi_model if pi_model is not None else agent_model
    return agent_model


def _make_gepa_proposer(
    run_dir: Path,
    *,
    agent_backend: str,
    model: str | None,
    timeout_seconds: float,
    pi_command: str,
    codex_command: str,
    sandbox: bool,
    codex_factory: Callable[..., Any] | None,
    pi_factory: Callable[..., Any] | None,
) -> Any:
    if agent_backend == "pi":
        return _make_pi_proposer(
            run_dir,
            model=model,
            timeout_seconds=timeout_seconds,
            pi_command=pi_command,
            sandbox=sandbox,
            factory=pi_factory,
        )
    return _make_codex_proposer(
        run_dir,
        model=model,
        timeout_seconds=timeout_seconds,
        codex_command=codex_command,
        sandbox=sandbox,
        factory=codex_factory,
    )


def _engine_config(
    engine: str,
    *,
    run_dir: Path,
    agent_backend: str,
    agent_model: str | None,
    codex_model: str | None,
    pi_model: str | None,
    pi_command: str,
    codex_command: str,
    claude_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    codex_proposer_factory: Callable[..., Any] | None,
    pi_proposer_factory: Callable[..., Any] | None,
    max_concurrency: int,
    sandbox: bool,
    gepa_parallel_proposals: tuple[int, int] | None,
) -> dict[str, Any]:
    if agent_backend not in AGENT_BACKENDS:
        raise ValueError(f"unsupported agent_backend {agent_backend!r}; choose " + ", ".join(AGENT_BACKENDS))
    if engine == "gepa":
        proposer = _make_gepa_proposer(
            run_dir / "proposer",
            agent_backend=agent_backend,
            model=_backend_model(
                agent_backend,
                agent_model=agent_model,
                codex_model=codex_model,
                pi_model=pi_model,
            ),
            timeout_seconds=codex_timeout_seconds,
            pi_command=pi_command,
            codex_command=codex_command,
            sandbox=sandbox,
            codex_factory=codex_proposer_factory,
            pi_factory=pi_proposer_factory,
        )
        engine_settings: dict[str, Any] = {"max_workers": 1, "parallel": False}
        if gepa_parallel_proposals is not None:
            from gepa.strategies.proposal_sampling import PxNSampling
            from gepa.strategies.proposal_selection import AllImprovements

            parents, mutations = gepa_parallel_proposals
            engine_settings.update(
                {
                    "max_workers": max_concurrency,
                    "parallel": True,
                    "sampling_strategy": PxNSampling(p=parents, n=mutations),
                    "selection_strategy": AllImprovements(),
                }
            )
        return {
            "engine": engine_settings,
            "reflection": {
                "reflection_lm": None,
                "custom_candidate_proposer": proposer,
                "module_selector": "all",
            },
        }
    if engine == "autoresearch":
        model = _backend_model(
            agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
        )
        config: dict[str, Any] = {
            "agent_backend": agent_backend,
            "ralph": True,
            "max_no_eval_seconds": 300,
            "timeout_seconds": codex_timeout_seconds,
        }
        if model is not None:
            config["model"] = model
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
        else:
            config["claude_command"] = claude_command
        return config
    if engine == "meta_harness":
        model = _backend_model(
            agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
        )
        config = {
            "agent_backend": agent_backend,
            "max_iterations": 20,
            "max_candidates_per_iter": 3,
            "timeout_seconds": codex_timeout_seconds,
        }
        if model is not None:
            config["model"] = model
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
        else:
            config["claude_command"] = claude_command
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
    pi_model: str | None,
    pi_command: str,
    codex_command: str,
    claude_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    codex_proposer_factory: Callable[..., Any] | None,
    pi_proposer_factory: Callable[..., Any] | None,
    gepa_parallel_proposals: tuple[int, int] | None,
) -> Any:
    if _is_pypi_config(config_cls):
        if engine != "gepa":
            raise ValueError(
                f"the selected GEPA launcher config supports only the explicit 'gepa' engine, not {engine!r}"
            )
        return _make_pypi_config(
            config_cls,
            budget,
            run_dir=run_dir,
            output_dir=output_dir,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            codex_command=codex_command,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_proposer_factory=codex_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
        )
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
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            gepa_parallel_proposals=gepa_parallel_proposals,
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


def _annotate_result(
    result: Any,
    *,
    continuation_engine: str,
    slices: tuple[BudgetSlice, ...],
    exploration_engines: tuple[str, ...] = _LEGACY_EXPLORATION_ENGINES,
) -> None:
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["omni"] = {
        "exploration_engines": list(exploration_engines),
        "continuation_engine": continuation_engine,
        "exploration_budget": asdict(slices[0]),
        "continuation_budget": asdict(slices[3]),
    }


def _with_auto_accounting(result: Any, *, pilot_evals: int, auto: Mapping[str, Any]) -> Any:
    """Add pilot evaluations without mutating a frozen engine result."""

    total_evals = int(getattr(result, "total_evals", 0)) + pilot_evals
    metadata = getattr(result, "metadata", None)
    params = getattr(result, "__dataclass_params__", None)
    if is_dataclass(result) and params is not None and params.frozen:
        merged = dict(metadata) if isinstance(metadata, dict) else {}
        merged["auto"] = dict(auto)
        return replace(result, total_evals=total_evals, metadata=merged)
    if isinstance(metadata, dict):
        metadata["auto"] = dict(auto)
    result.total_evals = total_evals
    return result


def _seed_score(seed_candidate: str, task: Mapping[str, Any]) -> float:
    from gepa_omni.api import _seed_score as score_seed

    return score_seed(seed_candidate, task)


def _auto_rank(result: Any, seed_score: float | None) -> float:
    """Rank a pilot. An engine that never scored cannot beat a measured score."""

    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("scored") is False:
        return float("-inf")
    score = float(getattr(result, "best_score", float("-inf")))
    evaluations = int(getattr(result, "total_evals", 0))
    if evaluations <= 0 or not math.isfinite(score):
        return float("-inf")
    improvement = score - seed_score if seed_score is not None else score
    return improvement / evaluations


def _run_auto(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    plan: Any,
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str,
    stop_at_score: float | None,
    max_concurrency: int,
    sandbox: bool,
    runner_profile: str,
    agent_model: str | None,
    codex_model: str | None,
    pi_model: str | None,
    pi_command: str,
    codex_command: str,
    claude_command: str,
    codex_input_cost_per_million: float | None,
    codex_output_cost_per_million: float | None,
    codex_timeout_seconds: float,
    launcher: Any | None,
    config_cls: Callable[..., Any] | None,
    codex_proposer_factory: Callable[..., Any] | None,
    pi_proposer_factory: Callable[..., Any] | None,
    gepa_parallel_proposals: tuple[int, int] | None,
    native_runner_factory: Callable[..., Any] | None,
    max_n: int | None,
    max_iterations: int | None,
    max_candidates_per_iter: int,
    ralph: bool,
) -> Any:
    """Pilot each eligible engine, then spend the remainder on the winner."""

    seed_score = _seed_score(seed_candidate, task)
    pilot_task = _task_for_phase(task, include_test_set=False)
    pilot_results: list[tuple[str, Any]] = []
    for index, pilot_engine in enumerate(plan.engines):
        result = run_optimization(
            seed_candidate,
            task=pilot_task,
            engine=pilot_engine,
            max_evals=plan.pilot_evals,
            max_token_cost=plan.pilot_token_cost,
            run_dir=Path(run_dir) / "auto" / "pilot" / pilot_engine,
            output_dir=Path(output_dir) / "auto" / "pilot" / pilot_engine,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            runner_profile=runner_profile,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            launcher=launcher,
            config_cls=config_cls,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
            native_runner_factory=native_runner_factory,
            max_n=max_n,
            max_iterations=max_iterations,
            max_candidates_per_iter=max_candidates_per_iter,
            ralph=ralph,
        )
        pilot_results.append((pilot_engine, result))

    def rank(item: tuple[str, Any]) -> float:
        return _auto_rank(item[1], seed_score)

    winning_engine, winning_result = max(pilot_results, key=rank)
    winning_candidate = getattr(winning_result, "best_candidate", None)
    if not isinstance(winning_candidate, str):
        raise TypeError("auto pilot returned no string best_candidate")
    if rank((winning_engine, winning_result)) == float("-inf"):
        raise ValueError("no auto pilot produced a measured finite score")
    continuation = continuation_engine or winning_engine
    result = run_optimization(
        winning_candidate,
        task=_task_for_phase(task, include_test_set=False),
        engine=continuation,
        max_evals=plan.continuation_evals,
        max_token_cost=plan.continuation_token_cost,
        run_dir=Path(run_dir) / "auto" / "continuation" / continuation,
        output_dir=Path(output_dir) / "auto" / "continuation" / continuation,
        stop_at_score=stop_at_score,
        max_concurrency=max_concurrency,
        sandbox=sandbox,
        runner_profile=runner_profile,
        agent_model=agent_model,
        codex_model=codex_model,
        pi_model=pi_model,
        pi_command=pi_command,
        codex_command=codex_command,
        claude_command=claude_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        launcher=launcher,
        config_cls=config_cls,
        codex_proposer_factory=codex_proposer_factory,
        pi_proposer_factory=pi_proposer_factory,
        gepa_parallel_proposals=gepa_parallel_proposals,
        native_runner_factory=native_runner_factory,
        max_n=max_n,
        max_iterations=max_iterations,
        max_candidates_per_iter=max_candidates_per_iter,
        ralph=ralph,
    )
    total_continuation_evals = int(getattr(result, "total_evals", 0))
    continuation_metadata = getattr(result, "metadata", None)
    continuation_unscored = isinstance(continuation_metadata, dict) and continuation_metadata.get("scored") is False
    if continuation_unscored or float(winning_result.best_score) > float(result.best_score):
        result = (
            replace(winning_result, total_evals=total_continuation_evals, metadata=dict(winning_result.metadata))
            if is_dataclass(winning_result)
            else SimpleNamespace(
                best_candidate=winning_result.best_candidate,
                best_score=winning_result.best_score,
                total_evals=total_continuation_evals,
                metadata=dict(getattr(winning_result, "metadata", {})),
            )
        )
    scores = _score_public_test_set(result.best_candidate, task)
    if scores:
        result.metadata.update(test_score=sum(scores) / len(scores), test_scores=scores)
    pilot_evals = sum(int(getattr(value, "total_evals", 0)) for _engine, value in pilot_results) + len(
        task.get("valset") or task.get("dataset") or [None]
    )
    return _with_auto_accounting(
        result,
        pilot_evals=pilot_evals,
        auto={
            "pilot_engines": list(plan.engines),
            "pilot_engine": winning_engine,
            "continuation_engine": continuation,
            "seed_score": seed_score,
            "pilot_scores": {engine: getattr(value, "best_score", None) for engine, value in pilot_results},
            "pilot_evals": pilot_evals,
            "max_evals": max_evals,
            "max_token_cost": max_token_cost,
        },
    )


def _run_legacy_omni(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str = _LEGACY_CONTINUATION_ENGINE,
    stop_at_score: float | None = None,
    max_concurrency: int = 1,
    sandbox: bool = True,
    runner_profile: str | None = None,
    agent_backend: str | None = None,
    agent_model: str | None = None,
    codex_model: str | None = None,
    pi_model: str | None = None,
    pi_command: str = "pi",
    codex_command: str = "codex",
    claude_command: str = "claude",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    launcher: Any | None = None,
    config_cls: Callable[..., Any] | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
    pi_proposer_factory: Callable[..., Any] | None = None,
    gepa_parallel_proposals: tuple[int, int] | None = None,
) -> Any:
    """Run Phase 1 exploration followed by a fresh Phase 2 continuation."""

    require_sandbox(sandbox)
    if not isinstance(seed_candidate, str):
        raise TypeError("seed_candidate must be a string")
    agent_backend = _resolve_runner_profile(runner_profile, agent_backend)
    if continuation_engine not in _LEGACY_EXPLORATION_ENGINES:
        raise ValueError("continuation_engine must be one of: " + ", ".join(_LEGACY_EXPLORATION_ENGINES))
    launcher, config_cls = _load_launcher(launcher, config_cls)
    _validate_max_concurrency(max_concurrency)
    gepa_parallel_proposals = _validate_parallel_proposals(gepa_parallel_proposals)
    slices = partition_budget(max_evals, max_token_cost)
    root = validate_external_path(run_dir, label="run_dir")
    output_root = validate_external_path(output_dir, label="output_dir")
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
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
        )
        for index, engine in enumerate(_LEGACY_EXPLORATION_ENGINES)
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
        pi_model=pi_model,
        pi_command=pi_command,
        codex_command=codex_command,
        claude_command=claude_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        codex_proposer_factory=codex_proposer_factory,
        pi_proposer_factory=pi_proposer_factory,
        gepa_parallel_proposals=gepa_parallel_proposals,
    )
    result = launcher.optimize_anything(
        best_candidate,
        **_task_for_phase(task, include_test_set=True),
        config=continuation_config,
    )
    _annotate_result(result, continuation_engine=continuation_engine, slices=slices)
    return result


def run_omni(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str | None = None,
    stop_at_score: float | None = None,
    max_concurrency: int = 1,
    sandbox: bool = True,
    runner_profile: str | None = None,
    agent_backend: str | None = None,
    agent_model: str | None = None,
    codex_model: str | None = None,
    pi_model: str | None = None,
    pi_command: str = "pi",
    codex_command: str = "codex",
    claude_command: str = "claude",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    launcher: Any | None = None,
    config_cls: Callable[..., Any] | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
    pi_proposer_factory: Callable[..., Any] | None = None,
    gepa_parallel_proposals: tuple[int, int] | None = None,
    native_runner_factory: Callable[..., Any] | None = None,
    max_n: int | None = None,
    max_iterations: int | None = None,
    max_candidates_per_iter: int = 3,
    ralph: bool = True,
) -> Any:
    """Run native Omni, or the injected legacy launcher compatibility seam.

    The normal path has no GEPA launcher dependency for the native branches:
    the first three slices run PyPI GEPA, AutoResearch, and Meta-Harness, and
    a fresh continuation receives the fourth. Passing an
    explicit launcher/config is retained for embedding callers that still use
    the historical source-backed composition API; it is never inferred.
    """

    require_sandbox(sandbox)
    _validate_max_concurrency(max_concurrency)
    agent_backend = _resolve_runner_profile(runner_profile, agent_backend)
    if launcher is not None or config_cls is not None:
        return _run_legacy_omni(
            seed_candidate,
            task=task,
            max_evals=max_evals,
            max_token_cost=max_token_cost,
            run_dir=run_dir,
            output_dir=output_dir,
            continuation_engine=continuation_engine or _LEGACY_CONTINUATION_ENGINE,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            runner_profile=agent_backend,
            agent_backend=None,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            launcher=launcher,
            config_cls=config_cls,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
        )
    if continuation_engine is None:
        continuation_engine = DEFAULT_CONTINUATION_ENGINE
    if continuation_engine != "gepa" and continuation_engine not in NATIVE_ENGINES:
        raise ValueError("continuation_engine must be one of: gepa, " + ", ".join(NATIVE_ENGINES))
    slices = partition_budget(max_evals, max_token_cost)
    from native_omni.coordinator import run_native_omni

    def _gepa_continuation(
        continuation_seed: str,
        *,
        task: Mapping[str, Any],
        budget: BudgetSlice,
        run_dir: Path,
        output_dir: Path,
    ) -> Any:
        # This is intentionally an explicit recursive call with
        # ``engine='gepa'``.  Native branches never import or infer PyPI GEPA.
        return run_optimization(
            continuation_seed,
            task=task,
            engine="gepa",
            max_evals=budget.max_evals,
            max_token_cost=budget.max_token_cost,
            run_dir=run_dir,
            output_dir=output_dir,
            stop_at_score=stop_at_score,
            max_concurrency=max_concurrency,
            sandbox=sandbox,
            agent_backend=agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
        )

    return run_native_omni(
        seed_candidate,
        task=task,
        slices=slices,
        run_dir=run_dir,
        output_dir=output_dir,
        continuation_engine=continuation_engine,
        stop_at_score=stop_at_score,
        sandbox=sandbox,
        agent_backend=agent_backend,
        agent_model=_backend_model(
            agent_backend,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
        ),
        codex_command=codex_command,
        pi_command=pi_command,
        claude_command=claude_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        runner_factory=native_runner_factory,
        max_n=max_n,
        max_iterations=max_iterations,
        max_candidates_per_iter=max_candidates_per_iter,
        ralph=ralph,
        # The callback is used for the PyPI GEPA Phase 1 branch in every
        # portfolio; it is also reused when GEPA is the selected continuation.
        gepa_continuation=_gepa_continuation,
    )


def run_optimization(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    engine: str | None = None,
    strategy: str | None = None,
    max_evals: int | None,
    max_token_cost: float | None,
    run_dir: str | Path,
    output_dir: str | Path,
    continuation_engine: str | None = None,
    stop_at_score: float | None = None,
    max_concurrency: int = 1,
    sandbox: bool = True,
    runner_profile: str | None = None,
    agent_backend: str | None = None,
    agent_model: str | None = None,
    codex_model: str | None = None,
    pi_model: str | None = None,
    pi_command: str = "pi",
    codex_command: str = "codex",
    claude_command: str = "claude",
    codex_input_cost_per_million: float | None = None,
    codex_output_cost_per_million: float | None = None,
    codex_timeout_seconds: float = 600.0,
    launcher: Any | None = None,
    config_cls: Callable[..., Any] | None = None,
    codex_proposer_factory: Callable[..., Any] | None = None,
    pi_proposer_factory: Callable[..., Any] | None = None,
    gepa_parallel_proposals: tuple[int, int] | None = None,
    native_runner_factory: Callable[..., Any] | None = None,
    max_n: int | None = None,
    max_iterations: int | None = None,
    max_candidates_per_iter: int = 3,
    ralph: bool = True,
) -> Any:
    """Run a standalone engine, fixed Omni, or adaptive ``auto`` strategy."""

    require_sandbox(sandbox)
    _validate_max_concurrency(max_concurrency)
    if not isinstance(seed_candidate, str):
        raise TypeError("seed_candidate must be a string")
    runner_profile = _resolve_runner_profile(runner_profile, agent_backend)
    gepa_parallel_proposals = _validate_parallel_proposals(gepa_parallel_proposals)
    selected_strategy = strategy or ("auto" if engine is None else "gepa")
    if continuation_engine is not None and continuation_engine not in STANDALONE_ENGINES:
        raise ValueError("unsupported continuation_engine")
    if selected_strategy == "gepa" and engine is None:
        engine = "gepa"
    if selected_strategy not in {"auto", "gepa", "omni"}:
        raise ValueError("strategy must be one of: auto, gepa, omni")
    if engine is not None and selected_strategy == "omni":
        raise ValueError("engine and strategy='omni' cannot be combined")
    if selected_strategy == "omni":
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
            runner_profile=runner_profile,
            agent_model=agent_model,
            codex_model=codex_model,
            pi_model=pi_model,
            pi_command=pi_command,
            codex_command=codex_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            launcher=launcher,
            config_cls=config_cls,
            codex_proposer_factory=codex_proposer_factory,
            pi_proposer_factory=pi_proposer_factory,
            gepa_parallel_proposals=gepa_parallel_proposals,
            native_runner_factory=native_runner_factory,
            max_n=max_n,
            max_iterations=max_iterations,
            max_candidates_per_iter=max_candidates_per_iter,
            ralph=ralph,
        )
    if selected_strategy == "auto" and engine is None:
        pool_size = len(task.get("valset") or task.get("dataset") or [None])
        plan = plan_auto_allocation(max_evals, max_token_cost, selection_pool_size=pool_size, baseline_evals=pool_size)
        if plan.fallback_engine is not None:
            engine = plan.fallback_engine
        else:
            return _run_auto(
                seed_candidate,
                task=task,
                plan=plan,
                max_evals=max_evals,
                max_token_cost=max_token_cost,
                run_dir=run_dir,
                output_dir=output_dir,
                continuation_engine=continuation_engine,
                stop_at_score=stop_at_score,
                max_concurrency=max_concurrency,
                sandbox=sandbox,
                runner_profile=runner_profile,
                agent_model=agent_model,
                codex_model=codex_model,
                pi_model=pi_model,
                pi_command=pi_command,
                codex_command=codex_command,
                claude_command=claude_command,
                codex_input_cost_per_million=codex_input_cost_per_million,
                codex_output_cost_per_million=codex_output_cost_per_million,
                codex_timeout_seconds=codex_timeout_seconds,
                launcher=launcher,
                config_cls=config_cls,
                codex_proposer_factory=codex_proposer_factory,
                pi_proposer_factory=pi_proposer_factory,
                gepa_parallel_proposals=gepa_parallel_proposals,
                native_runner_factory=native_runner_factory,
                max_n=max_n,
                max_iterations=max_iterations,
                max_candidates_per_iter=max_candidates_per_iter,
                ralph=ralph,
            )
    if engine not in STANDALONE_ENGINES:
        raise ValueError(
            f"unsupported standalone engine {engine!r}; omit engine for Omni or use " + ", ".join(STANDALONE_ENGINES)
        )
    if engine in NATIVE_ENGINES and launcher is None and config_cls is None:
        from native_omni.coordinator import run_native_engine

        return run_native_engine(
            seed_candidate,
            engine=engine,
            task=task,
            max_evals=max_evals,
            max_token_cost=max_token_cost,
            run_dir=Path(run_dir) / "standalone" / engine,
            output_dir=Path(output_dir) / "standalone" / engine,
            stop_at_score=stop_at_score,
            sandbox=sandbox,
            agent_backend=runner_profile,
            agent_model=_backend_model(
                runner_profile,
                agent_model=agent_model,
                codex_model=codex_model,
                pi_model=pi_model,
            ),
            codex_command=codex_command,
            pi_command=pi_command,
            claude_command=claude_command,
            codex_input_cost_per_million=codex_input_cost_per_million,
            codex_output_cost_per_million=codex_output_cost_per_million,
            codex_timeout_seconds=codex_timeout_seconds,
            runner_factory=native_runner_factory,
            max_n=max_n,
            max_iterations=max_iterations,
            max_candidates_per_iter=max_candidates_per_iter,
            ralph=ralph,
        )
    launcher, config_cls = _load_launcher(launcher, config_cls)
    budget = _full_budget(max_evals, max_token_cost)
    root = validate_external_path(run_dir, label="run_dir")
    output_root = validate_external_path(output_dir, label="output_dir")
    stage_dir = root / "standalone" / engine
    config = _make_config(
        config_cls,
        engine,
        budget,
        run_dir=stage_dir,
        output_dir=output_root / "standalone" / engine,
        stop_at_score=stop_at_score,
        max_concurrency=max_concurrency,
        sandbox=sandbox,
        agent_backend=runner_profile,
        agent_model=agent_model,
        codex_model=codex_model,
        pi_model=pi_model,
        pi_command=pi_command,
        codex_command=codex_command,
        claude_command=claude_command,
        codex_input_cost_per_million=codex_input_cost_per_million,
        codex_output_cost_per_million=codex_output_cost_per_million,
        codex_timeout_seconds=codex_timeout_seconds,
        codex_proposer_factory=codex_proposer_factory,
        pi_proposer_factory=pi_proposer_factory,
        gepa_parallel_proposals=gepa_parallel_proposals,
    )
    result = launcher.optimize_anything(
        seed_candidate,
        **_task_for_phase(task, include_test_set=not _is_pypi_config(config_cls)),
        config=config,
    )
    if _is_pypi_config(config_cls):
        return _PublicResult(
            result,
            task=task,
            run_dir=stage_dir,
            output_dir=output_root / "standalone" / engine,
        )
    return result
