from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import fleet_optimize
from gepa.strategies.proposal_sampling import PxNSampling, SameParentSampling
from gepa.strategies.proposal_selection import AllImprovements, BestImprovement, TopKImprovements


def _config_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "run_dir": tmp_path / "run",
        "output_dir": tmp_path / "output",
    }


def test_latest_surface_reports_configuration_and_parallel_symbols() -> None:
    report = fleet_optimize.describe_compatibility()

    assert report["config"] == "OptimizeAnythingConfig"
    assert report["batch_evaluator"] is True
    assert report["sampling"] == ["independent", "pxn", "same_parent"]
    assert report["selection"] == ["all", "best", "top_k"]
    assert "engine_config" in report["config_parameters"]
    assert "batch_evaluator" in report["optimize_parameters"]


def test_default_config_uses_pxn_two_by_two_and_all_improvements(tmp_path: Path) -> None:
    config = fleet_optimize.build_config(**_config_paths(tmp_path))
    engine = config.engine_config["engine"]
    sampling = engine["sampling_strategy"]

    assert isinstance(config, fleet_optimize._load_surface().config_cls)
    assert config.max_evals == 100
    assert config.max_concurrency == 8
    assert config.run_dir == str(tmp_path / "run")
    assert config.output_dir == str(tmp_path / "output")
    assert engine["parallel"] is True
    assert engine["max_workers"] == 8
    assert isinstance(sampling, PxNSampling)
    assert sampling.p == 2
    assert sampling.n == 2
    assert isinstance(engine["selection_strategy"], AllImprovements)


def test_explicit_sampling_selection_concurrency_and_metric_budget_pass_through(tmp_path: Path) -> None:
    sampling = SameParentSampling(n=3)
    selection = TopKImprovements(k=2)
    config = fleet_optimize.build_config(
        **_config_paths(tmp_path),
        sampling_strategy=sampling,
        selection_strategy=selection,
        max_evals=37,
        max_concurrency=5,
        engine_config={"max_workers": 11, "custom_engine_option": "keep"},
        reflection_config={"custom_reflection_option": "keep"},
    )
    engine = config.engine_config["engine"]

    assert config.max_evals == 37
    assert config.max_concurrency == 5
    assert engine["max_workers"] == 11
    assert engine["custom_engine_option"] == "keep"
    assert engine["sampling_strategy"] is sampling
    assert engine["selection_strategy"] is selection
    assert config.engine_config["reflection"]["custom_reflection_option"] == "keep"


def test_best_improvement_strategy_is_passed_through(tmp_path: Path) -> None:
    selection = BestImprovement()
    config = fleet_optimize.build_config(**_config_paths(tmp_path), selection_strategy=selection)

    assert config.engine_config["engine"]["selection_strategy"] is selection


def test_artifacts_must_be_absolute_and_outside_the_repository() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        fleet_optimize.build_config(run_dir="relative-run")

    repository_root = fleet_optimize.REPOSITORY_ROOT
    assert repository_root is not None
    with pytest.raises(ValueError, match="repository/plugin checkout"):
        fleet_optimize.build_config(run_dir=repository_root / "optimizer-run")


def test_generated_artifacts_reject_a_checkout_temp_root_before_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    repository_root = fleet_optimize.REPOSITORY_ROOT
    assert repository_root is not None
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(fleet_optimize.tempfile, "gettempdir", lambda: str(repository_root / "tmp"))

    def unexpected_mkdtemp(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(repository_root / "generated")

    monkeypatch.setattr(fleet_optimize.tempfile, "mkdtemp", unexpected_mkdtemp)

    with pytest.raises(ValueError, match="repository/plugin checkout"):
        fleet_optimize.build_config()

    assert calls == []


def test_generated_artifacts_validate_the_returned_temporary_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = fleet_optimize.REPOSITORY_ROOT
    assert repository_root is not None
    monkeypatch.setattr(fleet_optimize.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(fleet_optimize.tempfile, "mkdtemp", lambda **_kwargs: str(repository_root / "generated"))

    with pytest.raises(ValueError, match="repository/plugin checkout"):
        fleet_optimize.build_config()


def test_generated_artifacts_accept_external_temporary_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generated_paths = iter((tmp_path / "output", tmp_path / "run"))
    monkeypatch.setattr(fleet_optimize.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(fleet_optimize.tempfile, "mkdtemp", lambda **_kwargs: str(next(generated_paths)))

    config = fleet_optimize.build_config()

    assert config.output_dir == str(tmp_path / "output")
    assert config.run_dir == str(tmp_path / "run")


def test_build_config_uses_openai_model_when_reflection_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "provider-model")

    config = fleet_optimize.build_config(**_config_paths(tmp_path), reflection_lm="caller-model")
    default_config = fleet_optimize.build_config(**_config_paths(tmp_path))

    assert config.engine_config["reflection"]["reflection_lm"] == "caller-model"
    assert default_config.engine_config["reflection"]["reflection_lm"] == "provider-model"


def test_optimize_rewrites_home_relative_paths_before_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    surface = fleet_optimize._load_surface()
    config = surface.config_cls(engine="gepa", run_dir="~/runs", output_dir="~/outputs")
    captured: dict[str, object] = {}

    def fake_optimize_anything(**kwargs: object) -> str:
        captured.update(kwargs)
        return "rewritten"

    monkeypatch.setattr(
        fleet_optimize,
        "_load_surface",
        lambda: replace(surface, optimize_anything=fake_optimize_anything),
    )

    assert fleet_optimize.optimize("seed", evaluator=lambda *_args: 1.0, config=config) == "rewritten"
    forwarded = captured["config"]
    assert forwarded.run_dir == str((home / "runs").resolve())
    assert forwarded.output_dir == str((home / "outputs").resolve())


def test_optimize_forwards_native_arguments_and_batch_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fleet_optimize.build_config(**_config_paths(tmp_path))
    original_surface = fleet_optimize._load_surface()
    captured: dict[str, object] = {}

    def fake_optimize_anything(**kwargs: object) -> object:
        captured.update(kwargs)
        return "native-result"

    monkeypatch.setattr(
        fleet_optimize,
        "_load_surface",
        lambda: replace(original_surface, optimize_anything=fake_optimize_anything),
    )
    evaluator = lambda candidate, example: (1.0, {"candidate": candidate, "example": example})
    batch_evaluator = lambda pairs: [evaluator(candidate, example) for candidate, example in pairs]
    dataset = ["train-1", "train-2"]
    valset = ["validation"]
    test_set = ["held-out"]

    result = fleet_optimize.optimize(
        "seed",
        evaluator=evaluator,
        batch_evaluator=batch_evaluator,
        dataset=dataset,
        valset=valset,
        objective="improve",
        background="context",
        test_set=test_set,
        config=config,
    )

    assert result == "native-result"
    assert captured == {
        "seed_candidate": "seed",
        "evaluator": evaluator,
        "batch_evaluator": batch_evaluator,
        "dataset": dataset,
        "valset": valset,
        "objective": "improve",
        "background": "context",
        "test_set": test_set,
        "config": config,
    }


def test_batch_evaluator_order_and_cardinality_remain_upstream_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fleet_optimize.build_config(**_config_paths(tmp_path))
    original_surface = fleet_optimize._load_surface()
    seen: list[tuple[str, int]] = []

    def fake_optimize_anything(*, batch_evaluator, **_kwargs: object) -> list[float]:
        pairs = [("candidate-a", 1), ("candidate-b", 2)]
        values = list(batch_evaluator(pairs))
        if len(values) != len(pairs):
            raise ValueError("batch_evaluator returned one result per pair")
        return values

    monkeypatch.setattr(
        fleet_optimize,
        "_load_surface",
        lambda: replace(original_surface, optimize_anything=fake_optimize_anything),
    )

    def batch_evaluator(pairs: list[tuple[str, int]]) -> list[float]:
        seen.extend(pairs)
        return [float(example) for _candidate, example in pairs]

    assert fleet_optimize.optimize(config=config, batch_evaluator=batch_evaluator) == [1.0, 2.0]
    assert seen == [("candidate-a", 1), ("candidate-b", 2)]

    def bad_batch_evaluator(_pairs: list[tuple[str, int]]) -> list[float]:
        return [1.0]

    with pytest.raises(ValueError, match="one result per pair"):
        fleet_optimize.optimize(config=config, batch_evaluator=bad_batch_evaluator)


def test_legacy_or_unrelated_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="OptimizeAnythingConfig"):
        fleet_optimize.optimize(config=object())


def test_fleet_optimize_has_no_gepa_omni_internal_dependency() -> None:
    source = inspect.getsource(fleet_optimize)
    assert "gepa_omni" not in source
    assert "omni_pipeline" not in source
