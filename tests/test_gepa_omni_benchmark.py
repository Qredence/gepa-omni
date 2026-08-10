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


def test_fixture_benchmark_is_reproducible_and_equal_budgeted() -> None:
    benchmark = _load_benchmark()
    first = benchmark.run_fixture(seeds=(seed for seed in range(5)), max_evals=100, max_dollars=10.0)
    second = benchmark.run_fixture(seeds=range(5), max_evals=100, max_dollars=10.0)

    first_groups = [
        {key: value for key, value in group.items() if key != "wall_clock_seconds_median"} for group in first["groups"]
    ]
    second_groups = [
        {key: value for key, value in group.items() if key != "wall_clock_seconds_median"} for group in second["groups"]
    ]
    assert first_groups == second_groups
    assert len(first["groups"]) == 15
    assert {group["seeds"] for group in first["groups"]} == {5}
    assert {group["evaluations_median"] for group in first["groups"]} == {100}
    assert {group["dollars_median"] for group in first["groups"]} == {10.0}
    assert all("evaluations_to_target_median" in group for group in first["groups"])
    assert all("dollars_to_target_median" in group for group in first["groups"])
    assert all("selection_test_gap_median" in group for group in first["groups"])
