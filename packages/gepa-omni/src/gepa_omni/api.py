"""Public orchestration functions for GEPA Omni."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from functools import wraps
from threading import RLock
from dataclasses import dataclass, fields, replace
import math
import os
from numbers import Real
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from .allocation import plan_auto_allocation
from .config import OmniConfig
from .security import EndpointConfiguration, resolve_endpoint, use_resolved_endpoint


_ENDPOINT_LOCK = RLock()


def _serialize_endpoint_call(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _ENDPOINT_LOCK:
            return fn(*args, **kwargs)

    return call


@dataclass
class OptimizationResult:
    """Stable result view that retains an engine-specific raw result."""

    best_candidate: Any
    best_score: float
    total_evals: int
    metadata: dict[str, Any]
    raw: Any = None

    def __getattr__(self, name: str) -> Any:
        if self.raw is None:
            raise AttributeError(name)
        return getattr(self.raw, name)


def _task(
    evaluator: Callable[..., Any],
    *,
    batch_evaluator: Callable[..., Any] | None,
    dataset: Sequence[Any] | None,
    valset: Sequence[Any] | None,
    test_set: Sequence[Any] | None,
    objective: str | None,
    background: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"evaluator": evaluator}
    if batch_evaluator is not None:
        value["batch_evaluator"] = batch_evaluator
    if dataset is not None:
        value["dataset"] = list(dataset)
    if valset is not None:
        value["valset"] = list(valset)
    if test_set is not None:
        value["test_set"] = list(test_set)
    if objective is not None:
        value["objective"] = objective
    if background is not None:
        value["background"] = background
    return value


def _result(raw: Any, *, strategy: str, engine: str | None = None) -> OptimizationResult:
    candidate = getattr(raw, "best_candidate", None)
    score = getattr(raw, "best_score", None)
    evals = getattr(raw, "total_evals", None)
    if score is None and hasattr(raw, "val_aggregate_scores"):
        score = raw.val_aggregate_scores[raw.best_idx]
    if score is None:
        raise ValueError("optimization result did not include a score")
    if evals is None:
        evals = getattr(raw, "total_metric_calls", 0)
    metadata = dict(getattr(raw, "metadata", {}) or {})
    metadata.update({"strategy": strategy})
    if engine is not None:
        metadata.setdefault("engine", engine)
    best_score = float(score)
    if not math.isfinite(best_score):
        raise ValueError("optimization result score must be finite")
    return OptimizationResult(candidate, best_score, int(evals), metadata, raw)


def _legacy_runner() -> Any | None:
    try:
        from omni_pipeline import run_optimization as runner
    except ImportError:
        return None
    return runner


def _checkout_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists() and candidate not in roots:
                roots.append(candidate)
                break
    return tuple(roots)


def _validate_external_path(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute path outside the development checkout")
    resolved = candidate.resolve()
    if any(resolved == checkout or checkout in resolved.parents for checkout in _checkout_roots()):
        raise ValueError(f"{label} must be outside the development checkout")
    return resolved


@contextmanager
def _endpoint_environment(endpoint: EndpointConfiguration):
    """Expose the approved key to upstream GEPA only for the active call."""

    values = {
        "OPENAI_BASE_URL": endpoint.base_url,
        "OPENAI_MODEL": endpoint.model,
        "OPENAI_API_KEY": endpoint.api_key,
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _score_value(value: Any) -> float:
    if isinstance(value, (tuple, list)):
        if not value:
            raise ValueError("evaluator returned an empty result")
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("evaluator scores must be finite real numbers")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError("evaluator scores must be finite real numbers")
    return score


def _heldout_metadata(candidate: Any, task: Mapping[str, Any]) -> dict[str, Any]:
    examples = task.get("test_set")
    if not examples:
        return {}
    examples = list(examples)
    pairs = [(candidate, example) for example in examples]
    batch_evaluator = task.get("batch_evaluator")
    if callable(batch_evaluator):
        values = batch_evaluator(pairs)
        if values is None:
            raise TypeError("batch_evaluator must return one score per held-out example")
        scores = [_score_value(value) for value in values]
    else:
        evaluator = task.get("evaluator")
        if not callable(evaluator):
            raise TypeError("a callable evaluator is required to score test_set")
        scores = [_score_value(evaluator(candidate, example)) for example in examples]
    if len(scores) != len(examples):
        raise ValueError("batch_evaluator must return one score per held-out example")
    score = sum(scores) / len(scores)
    return {
        "heldout_scores": scores,
        "heldout_score": score,
        "test_scores": scores,
        "test_score": score,
        "heldout_num_evaluated": len(scores),
    }


STANDALONE_ENGINES = ("gepa", "autoresearch", "meta_harness", "best_of_n")


def _standalone_engine(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    config: OmniConfig,
    engine: str,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    score_heldout: bool = True,
) -> OptimizationResult:
    from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything

    if engine not in STANDALONE_ENGINES:
        raise ValueError(f"unsupported standalone engine {engine!r}; choose one of {STANDALONE_ENGINES}")
    if config.run_dir is None or config.output_dir is None:
        raise ValueError("run_dir and output_dir are required for optimization")
    endpoint = resolve_endpoint(
        base_url=base_url or config.base_url,
        model=config.model,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    run_root = _validate_external_path(config.run_dir, label="run_dir")
    output_root = _validate_external_path(config.output_dir, label="output_dir")
    engine_config: dict[str, Any] = {}
    from .transport import pinned_openai_client

    if engine in {"autoresearch", "meta_harness"}:
        raise ValueError(
            "standalone external agent runners cannot enforce endpoint pinning; use the plugin native runtime"
        )
    client = pinned_openai_client(endpoint)
    if engine == "gepa":
        engine_config["reflection"] = {"reflection_lm": endpoint.model, "reflection_lm_kwargs": {"client": client}}
    else:
        engine_config["model"] = endpoint.model
        engine_config["lm_kwargs"] = {"client": client}
    gepa_config = OptimizeAnythingConfig(
        engine=engine,
        max_evals=config.max_evals,
        max_token_cost=config.max_token_cost,
        max_concurrency=config.max_concurrency,
        run_dir=str(run_root / "standalone" / engine),
        output_dir=str(output_root / "standalone" / engine),
        stop_at_score=config.stop_at_score,
        sandbox=config.sandbox,
        engine_config=engine_config,
    )
    kwargs = dict(task)
    kwargs.pop("test_set", None)
    with client, _endpoint_environment(endpoint):
        raw = optimize_anything(seed_candidate, config=gepa_config, **kwargs)
        result = _result(raw, strategy=config.strategy, engine=engine)
        if score_heldout:
            result.metadata.update(_heldout_metadata(result.best_candidate, task))
    return result


def _standalone_gepa(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    config: OmniConfig,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> OptimizationResult:
    return _standalone_engine(
        seed_candidate,
        task=task,
        config=config,
        engine="gepa",
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )


def _selection_size(task: Mapping[str, Any]) -> int:
    return len(task.get("valset") or task.get("dataset") or [None])


def _seed_score(seed_candidate: str, task: Mapping[str, Any]) -> float:
    examples = list(task.get("valset") or task.get("dataset") or [None])
    batch = task.get("batch_evaluator")
    if callable(batch):
        scores = [_score_value(value) for value in batch([(seed_candidate, example) for example in examples])]
        if len(scores) != len(examples):
            raise ValueError("batch_evaluator must return one score per selection example")
    else:
        evaluator = task["evaluator"]
        scores = [
            _score_value(evaluator(seed_candidate) if example is None else evaluator(seed_candidate, example))
            for example in examples
        ]
    return sum(scores) / len(scores)


def _standalone_auto(
    seed_candidate: str,
    *,
    task: Mapping[str, Any],
    config: OmniConfig,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> OptimizationResult:
    baseline_evals = _selection_size(task)
    plan = plan_auto_allocation(
        config.max_evals,
        config.max_token_cost,
        selection_pool_size=baseline_evals,
        baseline_evals=baseline_evals,
        engines=("gepa", "best_of_n"),
    )
    if plan.fallback_engine is not None:
        fallback = replace(config, strategy=plan.fallback_engine)
        return _standalone_engine(
            seed_candidate,
            task=task,
            config=fallback,
            engine=plan.fallback_engine,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
        )

    endpoint = resolve_endpoint(
        base_url=base_url or config.base_url,
        model=config.model,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    with _endpoint_environment(endpoint):
        seed_score = _seed_score(seed_candidate, task)
    pilot_task = dict(task)
    pilot_task.pop("test_set", None)
    pilots: list[tuple[str, OptimizationResult]] = []
    for engine in plan.engines:
        pilot_config = replace(
            config,
            max_evals=plan.pilot_evals,
            max_token_cost=plan.pilot_token_cost,
            run_dir=str(Path(config.run_dir or "") / "auto" / "pilot" / engine),
            output_dir=str(Path(config.output_dir or "") / "auto" / "pilot" / engine),
        )
        pilots.append(
            (
                engine,
                _standalone_engine(
                    seed_candidate,
                    task=pilot_task,
                    config=pilot_config,
                    engine=engine,
                    base_url=base_url,
                    api_key=api_key,
                    api_key_env=api_key_env,
                    score_heldout=False,
                ),
            )
        )

    def rank(item: tuple[str, OptimizationResult]) -> float:
        _engine, result = item
        if result.metadata.get("scored") is False or result.total_evals <= 0 or not math.isfinite(result.best_score):
            return float("-inf")
        improvement = result.best_score - seed_score if seed_score is not None else result.best_score
        return improvement / max(1, result.total_evals)

    winning_engine, winning_result = max(pilots, key=rank)
    if rank((winning_engine, winning_result)) == float("-inf"):
        raise ValueError("no auto pilot produced a measured finite score")
    continuation = config.continuation_engine or winning_engine
    continuation_config = replace(
        config,
        max_evals=plan.continuation_evals,
        max_token_cost=plan.continuation_token_cost,
        run_dir=str(Path(config.run_dir or "") / "auto" / "continuation" / continuation),
        output_dir=str(Path(config.output_dir or "") / "auto" / "continuation" / continuation),
    )
    result = _standalone_engine(
        winning_result.best_candidate,
        task=task,
        config=continuation_config,
        engine=continuation,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        score_heldout=False,
    )
    if winning_result.best_score > result.best_score:
        result.best_candidate = winning_result.best_candidate
        result.best_score = winning_result.best_score
    result.metadata.update(_heldout_metadata(result.best_candidate, task))
    pilot_evals = sum(pilot.total_evals for _engine, pilot in pilots)
    result.total_evals += pilot_evals + baseline_evals
    result.metadata["auto"] = {
        "pilot_engines": list(plan.engines),
        "pilot_engine": winning_engine,
        "continuation_engine": continuation,
        "seed_score": seed_score,
        "pilot_scores": {engine: pilot.best_score for engine, pilot in pilots},
    }
    return result


def _call_plugin_runner(
    runner: Any,
    seed_candidate: str,
    options: dict[str, Any],
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> Any:
    """Give the plugin the endpoint the caller supplied, not only the process environment."""

    explicit = any(isinstance(value, str) and value.strip() for value in (base_url, model, api_key, api_key_env))
    if not explicit:
        return runner(seed_candidate, **options)
    endpoint = resolve_endpoint(base_url=base_url, model=model, api_key=api_key, api_key_env=api_key_env)
    with use_resolved_endpoint(endpoint):
        return runner(seed_candidate, **options)


@_serialize_endpoint_call
def run_optimization(
    seed_candidate: str,
    *,
    task: Mapping[str, Any] | None = None,
    evaluator: Callable[..., Any] | None = None,
    dataset: Sequence[Any] | None = None,
    valset: Sequence[Any] | None = None,
    test_set: Sequence[Any] | None = None,
    objective: str | None = None,
    background: str | None = None,
    config: OmniConfig | None = None,
    strategy: str | None = None,
    engine: str | None = None,
    **legacy_options: Any,
) -> OptimizationResult:
    """Run GEPA Omni through the stable package API.

    The plugin checkout supplies the native engine adapter through the
    compatibility shim. A clean package installation always retains the
    standalone GEPA path, so public imports do not depend on repository
    script paths.
    """

    if not isinstance(seed_candidate, str):
        raise TypeError("seed_candidate must be a string")
    if config is None:
        config_names = {field.name for field in fields(OmniConfig)}
        config_values = {name: legacy_options.pop(name) for name in tuple(legacy_options) if name in config_names}
        selected = OmniConfig(strategy=(strategy or "auto"), **config_values)
    else:
        selected = config
    base_url = legacy_options.pop("base_url", None)
    if base_url is not None and selected.base_url is not None and base_url != selected.base_url:
        raise ValueError("base_url conflicts with config.base_url")
    base_url = base_url or selected.base_url
    api_key = legacy_options.pop("api_key", None)
    api_key_env = legacy_options.pop("api_key_env", None)
    if strategy is not None and strategy != selected.strategy:
        raise ValueError("strategy conflicts with config.strategy")
    if task is None:
        if evaluator is None:
            raise TypeError("evaluator is required when task is not supplied")
        task = _task(
            evaluator,
            batch_evaluator=legacy_options.pop("batch_evaluator", None),
            dataset=dataset,
            valset=valset,
            test_set=test_set,
            objective=objective,
            background=background,
        )
    else:
        task = dict(task)
    selected_engine = engine
    if engine is not None:
        selected = OmniConfig(**{**selected.__dict__, "strategy": "gepa" if engine == "gepa" else selected.strategy})
    elif selected.strategy == "gepa":
        selected_engine = "gepa"
    selected = OmniConfig(
        **{
            **selected.__dict__,
            "run_dir": str(
                _validate_external_path(
                    selected.run_dir or mkdtemp(prefix="gepa-omni-run-"),
                    label="run_dir",
                )
            ),
            "output_dir": str(
                _validate_external_path(
                    selected.output_dir or mkdtemp(prefix="gepa-omni-output-"),
                    label="output_dir",
                )
            ),
        }
    )
    runner = _legacy_runner()
    if runner is not None:
        options = dict(legacy_options)
        options.pop("batch_evaluator", None)
        options.update(
            {
                "task": task,
                "strategy": selected.strategy,
                "engine": selected_engine,
                "max_evals": selected.max_evals,
                "max_token_cost": selected.max_token_cost,
                "run_dir": selected.run_dir,
                "output_dir": selected.output_dir,
                "runner_profile": selected.runner_profile,
                "agent_model": selected.model or options.get("agent_model"),
                "max_concurrency": selected.max_concurrency,
                "stop_at_score": selected.stop_at_score,
                "sandbox": selected.sandbox,
                "continuation_engine": selected.continuation_engine,
            }
        )
        options.setdefault("codex_input_cost_per_million", selected.input_cost_per_million)
        options.setdefault("codex_output_cost_per_million", selected.output_cost_per_million)
        raw = _call_plugin_runner(
            runner,
            seed_candidate,
            options,
            base_url=base_url,
            model=selected.model,
            api_key=api_key,
            api_key_env=api_key_env,
        )
        return _result(raw, strategy=selected.strategy, engine=selected_engine)
    if legacy_options:
        raise TypeError("unexpected standalone options: " + ", ".join(sorted(legacy_options)))
    if selected.strategy == "omni":
        raise RuntimeError("strategy='omni' requires the GEPA Omni plugin runtime; install the plugin adapter")
    if selected.strategy == "auto" and selected_engine is None:
        return _standalone_auto(
            seed_candidate,
            task=task,
            config=selected,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
        )
    selected_engine = selected_engine or "gepa"
    return _standalone_engine(
        seed_candidate,
        task=task,
        config=selected,
        engine=selected_engine,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )


def optimize(
    seed_candidate: str,
    *,
    evaluator: Callable[..., Any],
    dataset: Sequence[Any] | None = None,
    valset: Sequence[Any] | None = None,
    test_set: Sequence[Any] | None = None,
    objective: str | None = None,
    background: str | None = None,
    config: OmniConfig | None = None,
    **options: Any,
) -> OptimizationResult:
    """Optimize a candidate using the public evaluator-first interface."""

    return run_optimization(
        seed_candidate,
        evaluator=evaluator,
        dataset=dataset,
        valset=valset,
        test_set=test_set,
        objective=objective,
        background=background,
        config=config,
        **options,
    )


__all__ = ["OptimizationResult", "optimize", "run_optimization"]
