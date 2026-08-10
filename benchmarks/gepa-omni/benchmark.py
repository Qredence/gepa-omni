"""Reproducible GEPA Omni comparison harness.

The harness is credential-free by default. A live adapter can be supplied by
the caller, but the aggregation contract is identical for fixture and live
runs: equal seeds, splits, evaluation budgets, and proposer-cost caps.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENGINES = ("gepa", "autoresearch", "meta_harness", "best_of_n", "omni")
CASE_NAMES = ("prompt", "code_config", "noisy_judge")


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    seed_candidate: str
    train: tuple[dict[str, Any], ...]
    validation: tuple[dict[str, Any], ...]
    test: tuple[dict[str, Any], ...]
    target: float


@dataclass(frozen=True)
class BenchmarkRun:
    case: str
    engine: str
    seed: int
    heldout_improvement: float
    evaluations: int
    dollars: float
    wall_clock_seconds: float
    failed: bool
    selection_test_gap: float
    reached_target: bool
    evaluations_to_target: int | None
    dollars_to_target: float | None


def benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            "prompt",
            "Answer clearly.",
            ({"input": "2+2", "gold": "4"},),
            ({"input": "capital of France", "gold": "Paris"},),
            ({"input": "largest planet", "gold": "Jupiter"},),
            0.6,
        ),
        BenchmarkCase(
            "code_config",
            "retries=1\ntimeout=5",
            ({"expected": {"retries": 2, "timeout": 10}},),
            ({"expected": {"retries": 3, "timeout": 10}},),
            ({"expected": {"retries": 3, "timeout": 15}},),
            0.6,
        ),
        BenchmarkCase(
            "noisy_judge",
            "Be concise.",
            ({"rubric": "correctness"},),
            ({"rubric": "clarity"},),
            ({"rubric": "usefulness"},),
            0.6,
        ),
    )


def aggregate(runs: Iterable[BenchmarkRun]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[BenchmarkRun]] = {}
    for run in runs:
        grouped.setdefault((run.case, run.engine), []).append(run)
    report: dict[str, Any] = {"schema_version": 1, "groups": []}
    for (case, engine), values in sorted(grouped.items()):
        improvements = [value.heldout_improvement for value in values]
        report["groups"].append(
            {
                "case": case,
                "engine": engine,
                "seeds": len(values),
                "heldout_improvement_median": statistics.median(improvements),
                "heldout_improvement_variance": statistics.pvariance(improvements) if len(values) > 1 else 0.0,
                "evaluations_median": statistics.median(value.evaluations for value in values),
                "dollars_median": statistics.median(value.dollars for value in values),
                "wall_clock_seconds_median": statistics.median(value.wall_clock_seconds for value in values),
                "failure_rate": sum(value.failed for value in values) / len(values),
                "target_rate": sum(value.reached_target for value in values) / len(values),
                "evaluations_to_target_median": _median_or_none(
                    value.evaluations_to_target for value in values if value.evaluations_to_target is not None
                ),
                "dollars_to_target_median": _median_or_none(
                    value.dollars_to_target for value in values if value.dollars_to_target is not None
                ),
                "selection_test_gap_median": statistics.median(value.selection_test_gap for value in values),
            }
        )
    return report


def _median_or_none(values: Iterable[int | float]) -> float | None:
    values = list(values)
    return statistics.median(values) if values else None


def fixture_runner(engine: str, case: BenchmarkCase, seed: int, *, max_evals: int, max_dollars: float) -> BenchmarkRun:
    """Run a deterministic local fixture used by CI and harness smoke tests."""

    started = time.perf_counter()
    engine_bonus = {"gepa": 0.55, "autoresearch": 0.60, "meta_harness": 0.58, "best_of_n": 0.50, "omni": 0.63}[engine]
    noise = ((seed * 17 + len(case.name) * 3) % 11 - 5) / 100.0
    heldout = max(0.0, min(1.0, engine_bonus + noise))
    selection = max(0.0, min(1.0, heldout + 0.02))
    reached_target = heldout >= case.target
    evaluations_to_target = max(1, math.ceil(max_evals * case.target / heldout)) if reached_target and heldout else None
    dollars_to_target = max_dollars * evaluations_to_target / max_evals if evaluations_to_target is not None else None
    return BenchmarkRun(
        case=case.name,
        engine=engine,
        seed=seed,
        heldout_improvement=heldout,
        evaluations=max_evals,
        dollars=max_dollars,
        wall_clock_seconds=time.perf_counter() - started,
        failed=False,
        selection_test_gap=selection - heldout,
        reached_target=reached_target,
        evaluations_to_target=evaluations_to_target,
        dollars_to_target=dollars_to_target,
    )


def run_fixture(*, seeds: Iterable[int] = range(5), max_evals: int = 100, max_dollars: float = 10.0) -> dict[str, Any]:
    seed_values = tuple(seeds)
    runs = [
        fixture_runner(engine, case, seed, max_evals=max_evals, max_dollars=max_dollars)
        for case in benchmark_cases()
        for engine in ENGINES
        for seed in seed_values
    ]
    return aggregate(runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-evals", type=int, default=100)
    parser.add_argument("--max-dollars", type=float, default=10.0)
    args = parser.parse_args(argv)
    report = run_fixture(max_evals=args.max_evals, max_dollars=args.max_dollars)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
