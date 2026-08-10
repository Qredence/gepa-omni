from __future__ import annotations

from pathlib import Path

from fleet_optimize import build_config, describe_compatibility
from gepa.strategies.proposal_sampling import PxNSampling
from gepa.strategies.proposal_selection import AllImprovements


def test_shipped_runtime_exposes_latest_parallel_surface(tmp_path: Path) -> None:
    report = describe_compatibility()
    config = build_config(run_dir=tmp_path / "run", output_dir=tmp_path / "output")
    engine = config.engine_config["engine"]

    assert report["batch_evaluator"] is True
    assert report["config"] == "OptimizeAnythingConfig"
    assert isinstance(engine["sampling_strategy"], PxNSampling)
    assert isinstance(engine["selection_strategy"], AllImprovements)
