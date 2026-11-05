"""Latest-upstream GEPA launcher for the Fleet Optimize skill."""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import ModuleType
from typing import Any, get_args, get_type_hints


PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _find_checkout_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


REPOSITORY_ROOT = _find_checkout_root(PLUGIN_ROOT)


class FleetOptimizeCompatibilityError(RuntimeError):
    """Raised when the installed GEPA does not expose the latest API."""


@dataclass(frozen=True)
class UpstreamSurface:
    """Resolved latest GEPA symbols used by Fleet Optimize."""

    package_version: str
    package_revision: str | None
    package_file: str
    optimize_anything: Callable[..., Any]
    config_cls: type[Any]
    sampling: Mapping[str, type[Any]]
    selection: Mapping[str, type[Any]]


def _resolve_config_class(api_module: ModuleType, optimize_anything: Callable[..., Any]) -> type[Any]:
    """Find the current config class without selecting the legacy GEPAConfig."""

    candidates: list[object] = list(vars(api_module).values())
    candidates.extend(getattr(optimize_anything, "__globals__", {}).values())
    try:
        candidates.extend(get_type_hints(optimize_anything).values())
    except (NameError, TypeError):
        pass

    required_fields = {"engine", "max_evals", "max_concurrency", "engine_config"}
    seen: set[int] = set()
    for candidate in candidates:
        for config_candidate in (candidate, *get_args(candidate)):
            if id(config_candidate) in seen or not inspect.isclass(config_candidate):
                continue
            seen.add(id(config_candidate))
            if not str(getattr(config_candidate, "__module__", "")).startswith("gepa."):
                continue
            try:
                parameters = inspect.signature(config_candidate).parameters
            except (TypeError, ValueError):
                continue
            if required_fields.issubset(parameters):
                return config_candidate
    raise FleetOptimizeCompatibilityError(
        "Installed GEPA does not expose a current engine-pluggable config with "
        "engine, max_evals, max_concurrency, and engine_config fields. "
        "Install the latest upstream GEPA."
    )


def _load_surface() -> UpstreamSurface:
    """Load and validate the engine-pluggable upstream surface."""

    try:
        import gepa
        import gepa.optimize_anything as api_module
        from gepa.strategies.proposal_sampling import (
            IndependentSampling,
            PxNSampling,
            SameParentSampling,
        )
        from gepa.strategies.proposal_selection import (
            AllImprovements,
            BestImprovement,
            TopKImprovements,
        )

        optimize_anything = api_module.optimize_anything
        config_cls = _resolve_config_class(api_module, optimize_anything)
    except FleetOptimizeCompatibilityError:
        raise
    except Exception as exc:  # pragma: no cover - exact import error is environment-dependent
        raise FleetOptimizeCompatibilityError(
            "Fleet Optimize requires the latest upstream GEPA surface with a "
            "current engine-pluggable config, proposal sampling/selection strategies, "
            "and optimize_anything(batch_evaluator=...). Install the latest "
            "release or `gepa[full] @ git+https://github.com/gepa-ai/gepa.git@8a2bed96385202f69caaeb5327a843ed2f5ea225`."
        ) from exc

    try:
        parameters = inspect.signature(optimize_anything).parameters
        config_parameters = inspect.signature(config_cls).parameters
    except (TypeError, ValueError) as exc:
        raise FleetOptimizeCompatibilityError("Could not inspect upstream optimize_anything") from exc
    if "batch_evaluator" not in parameters:
        raise FleetOptimizeCompatibilityError(
            "Installed GEPA optimize_anything has no batch_evaluator parameter; install the latest upstream GEPA."
        )
    required_config_fields = {"engine", "max_evals", "max_concurrency", "engine_config"}
    missing_config_fields = sorted(required_config_fields - set(config_parameters))
    if missing_config_fields:
        raise FleetOptimizeCompatibilityError(
            "Installed GEPA engine-pluggable config is incompatible; missing fields: "
            + ", ".join(missing_config_fields)
            + ". Install the latest upstream GEPA."
        )

    try:
        package_version = importlib_metadata.version("gepa")
    except importlib_metadata.PackageNotFoundError:
        package_version = str(getattr(gepa, "__version__", "unknown"))
    package_revision: str | None = None
    try:
        distribution = importlib_metadata.distribution("gepa")
        direct_url = distribution.read_text("direct_url.json")
        if direct_url:
            direct_url_data = json.loads(direct_url)
            if isinstance(direct_url_data, dict):
                vcs_info = direct_url_data.get("vcs_info")
                if isinstance(vcs_info, dict):
                    package_revision = vcs_info.get("commit_id")
    except (importlib_metadata.PackageNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        package_revision = None

    return UpstreamSurface(
        package_version=package_version,
        package_revision=package_revision,
        package_file=str(getattr(gepa, "__file__", "unknown")),
        optimize_anything=optimize_anything,
        config_cls=config_cls,
        sampling={
            "same_parent": SameParentSampling,
            "independent": IndependentSampling,
            "pxn": PxNSampling,
        },
        selection={
            "all": AllImprovements,
            "best": BestImprovement,
            "top_k": TopKImprovements,
        },
    )


def describe_compatibility() -> dict[str, Any]:
    """Return non-secret diagnostics for the upstream compatibility gate."""

    surface = _load_surface()
    return {
        "gepa_version": surface.package_version,
        "gepa_revision": surface.package_revision,
        "gepa_file": surface.package_file,
        "config": surface.config_cls.__name__,
        "config_parameters": sorted(inspect.signature(surface.config_cls).parameters),
        "optimize_parameters": sorted(inspect.signature(surface.optimize_anything).parameters),
        "sampling": sorted(surface.sampling),
        "selection": sorted(surface.selection),
        "batch_evaluator": True,
    }


def _validate_positive_int(value: int, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _validate_external_path(value: str | Path, *, label: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path outside the repository/plugin checkout")
    resolved = path.resolve()
    forbidden_roots = [PLUGIN_ROOT]
    if REPOSITORY_ROOT is not None:
        forbidden_roots.append(REPOSITORY_ROOT)
    if any(resolved == root or root in resolved.parents for root in forbidden_roots):
        raise ValueError(f"{label} must be outside the repository/plugin checkout")
    return str(resolved)


def _external_path(value: str | Path | None, *, label: str, prefix: str) -> str:
    if value is None:
        _validate_external_path(tempfile.gettempdir(), label=label)
        value = tempfile.mkdtemp(prefix=prefix)
    return _validate_external_path(value, label=label)


def _copy_nested_mapping(values: Mapping[str, Any] | None, *, label: str) -> dict[str, Any]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return dict(values)


def build_config(
    *,
    parents: int = 2,
    mutations: int = 2,
    sampling_strategy: Any | None = None,
    selection_strategy: Any | None = None,
    max_evals: int | None = 100,
    max_token_cost: float | None = None,
    max_concurrency: int = 8,
    run_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    stop_at_score: float | None = None,
    reflection_lm: Any | None = None,
    reflection_lm_kwargs: Mapping[str, Any] | None = None,
    engine_config: Mapping[str, Any] | None = None,
    reflection_config: Mapping[str, Any] | None = None,
) -> Any:
    """Build the latest upstream config with Fleet Optimize defaults.

    ``engine_config`` and ``reflection_config`` are mappings for the respective
    nested upstream config blocks. Their explicit keys win over helper defaults, which keeps the upstream escape
    hatch available without silently discarding caller settings.
    """

    _validate_positive_int(parents, label="parents")
    _validate_positive_int(mutations, label="mutations")
    _validate_positive_int(max_concurrency, label="max_concurrency")
    if max_evals is not None:
        _validate_positive_int(max_evals, label="max_evals")

    surface = _load_surface()
    engine_values = _copy_nested_mapping(engine_config, label="engine_config")
    reflection_values = _copy_nested_mapping(reflection_config, label="reflection_config")
    default_sampling = (
        sampling_strategy if sampling_strategy is not None else surface.sampling["pxn"](p=parents, n=mutations)
    )
    default_selection = selection_strategy if selection_strategy is not None else surface.selection["all"]()
    engine_values.setdefault("parallel", True)
    engine_values.setdefault("max_workers", max_concurrency)
    engine_values.setdefault("sampling_strategy", default_sampling)
    engine_values.setdefault("selection_strategy", default_selection)
    if reflection_lm is not None:
        reflection_values.setdefault("reflection_lm", reflection_lm)
    elif "reflection_lm" not in reflection_values:
        env_model = os.environ.get("OPENAI_MODEL", "").strip()
        if env_model:
            reflection_values["reflection_lm"] = env_model
    if reflection_lm_kwargs is not None:
        reflection_values.setdefault("reflection_lm_kwargs", dict(reflection_lm_kwargs))

    return surface.config_cls(
        engine="gepa",
        max_evals=max_evals,
        max_token_cost=max_token_cost,
        max_concurrency=max_concurrency,
        output_dir=_external_path(output_dir, label="output_dir", prefix="fleet-optimize-output-"),
        run_dir=_external_path(run_dir, label="run_dir", prefix="fleet-optimize-run-"),
        stop_at_score=stop_at_score,
        engine_config={"engine": engine_values, "reflection": reflection_values},
    )


def _validate_config(config: Any, surface: UpstreamSurface) -> Any:
    if not isinstance(config, surface.config_cls):
        raise TypeError(
            f"Fleet Optimize requires the latest upstream OptimizeAnythingConfig; got {type(config).__name__}"
        )
    updates: dict[str, str] = {}
    for label in ("run_dir", "output_dir"):
        value = getattr(config, label, None)
        if value is not None:
            resolved = _validate_external_path(value, label=label)
            if resolved != value:
                updates[label] = resolved
    if not updates:
        return config
    return replace(config, **updates)


def optimize(
    seed_candidate: str | dict[str, str] | None = None,
    *,
    evaluator: Callable[..., Any] | None = None,
    batch_evaluator: Callable[..., Any] | None = None,
    dataset: list[Any] | None = None,
    valset: list[Any] | None = None,
    objective: str | None = None,
    background: str | None = None,
    test_set: list[Any] | None = None,
    config: Any | None = None,
) -> Any:
    """Run the latest upstream GEPA engine with safe Fleet defaults."""

    surface = _load_surface()
    effective_config = _validate_config(config if config is not None else build_config(), surface)
    return surface.optimize_anything(
        seed_candidate=seed_candidate,
        evaluator=evaluator,
        batch_evaluator=batch_evaluator,
        dataset=dataset,
        valset=valset,
        objective=objective,
        background=background,
        test_set=test_set,
        config=effective_config,
    )


__all__ = [
    "FleetOptimizeCompatibilityError",
    "PLUGIN_ROOT",
    "build_config",
    "describe_compatibility",
    "optimize",
]
