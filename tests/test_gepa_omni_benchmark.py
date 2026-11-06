from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "gepa-omni" / "benchmark.py"


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("gepa_omni_benchmark", BENCHMARK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_benchmark_is_reproducible_and_reports_no_engine_measurements() -> None:
    benchmark = _load_benchmark()
    first = benchmark.run_fixture(seeds=(seed for seed in range(5)))
    second = benchmark.run_fixture(seeds=range(5))

    first_groups = [
        {key: value for key, value in group.items() if key != "wall_clock_seconds_median"} for group in first["groups"]
    ]
    second_groups = [
        {key: value for key, value in group.items() if key != "wall_clock_seconds_median"} for group in second["groups"]
    ]
    assert first_groups == second_groups
    assert first["mode"] == "fixture"
    assert "no optimization engines or evaluators were run" in first["description"]
    assert len(first["groups"]) == 15
    assert {group["seeds"] for group in first["groups"]} == {5}
    assert all(group["heldout_improvement_median"] is None for group in first["groups"])
    assert all(group["evaluations_median"] is None for group in first["groups"])
    assert all(group["dollars_median"] is None for group in first["groups"])
    assert all(group["wall_clock_seconds_median"] is None for group in first["groups"])
    assert all(group["failure_rate"] is None for group in first["groups"])
    assert all(group["target_rate"] is None for group in first["groups"])
    assert all("evaluations_to_target_median" in group for group in first["groups"])
    assert all("dollars_to_target_median" in group for group in first["groups"])
    assert all("selection_test_gap_median" in group for group in first["groups"])
