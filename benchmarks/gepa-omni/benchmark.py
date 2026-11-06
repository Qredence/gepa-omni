"""Credential-free report-shape smoke harness for GEPA Omni.

Fixture mode does not run optimizers or evaluators and emits no performance
measurements. Real engine comparisons require a separate live adapter.
"""

from __future__ import annotations

import argparse
import json
import statistics
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
    heldout_improvement: float | None
    evaluations: int | None
    dollars: float | None
    wall_clock_seconds: float | None
    failed: bool | None
    selection_test_gap: float | None
    reached_target: bool | None
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
    report: dict[str, Any] = {"schema_version": 2, "groups": []}
    for (case, engine), values in sorted(grouped.items()):
        improvements = [value.heldout_improvement for value in values if value.heldout_improvement is not None]
        report["groups"].append(
            {
                "case": case,
                "engine": engine,
                "seeds": len(values),
                "heldout_improvement_median": _median_or_none(improvements),
                "heldout_improvement_variance": (
                    statistics.pvariance(improvements) if len(improvements) > 1 else 0.0 if improvements else None
                ),
                "evaluations_median": _median_or_none(value.evaluations for value in values if value.evaluations is not None),
                "dollars_median": _median_or_none(value.dollars for value in values if value.dollars is not None),
                "wall_clock_seconds_median": _median_or_none(
                    value.wall_clock_seconds for value in values if value.wall_clock_seconds is not None
                ),
                "failure_rate": (
                    sum(value.failed for value in values if value.failed is not None)
                    / sum(value.failed is not None for value in values)
                    if any(value.failed is not None for value in values)
                    else None
                ),
                "target_rate": (
                    sum(value.reached_target for value in values if value.reached_target is not None)
                    / sum(value.reached_target is not None for value in values)
                    if any(value.reached_target is not None for value in values)
                    else None
                ),
                "evaluations_to_target_median": _median_or_none(
                    value.evaluations_to_target for value in values if value.evaluations_to_target is not None
                ),
                "dollars_to_target_median": _median_or_none(
                    value.dollars_to_target for value in values if value.dollars_to_target is not None
                ),
                "selection_test_gap_median": _median_or_none(
                    value.selection_test_gap for value in values if value.selection_test_gap is not None
                ),
            }
        )
    return report


def _median_or_none(values: Iterable[int | float]) -> float | None:
    values = list(values)
    return statistics.median(values) if values else None


def fixture_runner(engine: str, case: BenchmarkCase, seed: int) -> BenchmarkRun:
    """Return a smoke-run record without fabricating engine measurements."""

    if engine not in ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    return BenchmarkRun(
        case=case.name,
        engine=engine,
        seed=seed,
        heldout_improvement=None,
        evaluations=None,
        dollars=None,
        wall_clock_seconds=None,
        failed=None,
        selection_test_gap=None,
        reached_target=None,
        evaluations_to_target=None,
        dollars_to_target=None,
    )


def run_fixture(*, seeds: Iterable[int] = range(5)) -> dict[str, Any]:
    seed_values = tuple(seeds)
    runs = [
        fixture_runner(engine, case, seed)
        for case in benchmark_cases()
        for engine in ENGINES
        for seed in seed_values
    ]
    report = aggregate(runs)
    report["mode"] = "fixture"
    report["description"] = "Schema smoke run only; no optimization engines or evaluators were run."
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_fixture()
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
