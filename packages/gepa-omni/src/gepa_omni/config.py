"""Typed configuration for the public GEPA Omni API."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


Strategy = Literal["gepa", "omni", "auto"]


@dataclass(frozen=True)
class OmniConfig:
    """Configuration shared by standalone and portfolio optimization.

    ``auto`` is deliberately the default: it pilots eligible engines and
    falls back to standalone GEPA when the supplied budget is too small for a
    useful portfolio. ``omni`` is the fixed portfolio experiment.
    """

    strategy: Strategy = "auto"
    max_evals: int | None = None
    max_token_cost: float | None = None
    model: str | None = None
    base_url: str | None = None
    runner_profile: str = "default"
    max_concurrency: int = 1
    stop_at_score: float | None = None
    sandbox: bool = True
    run_dir: str | None = None
    output_dir: str | None = None
    continuation_engine: str | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def __post_init__(self) -> None:
        if self.continuation_engine not in {None, "gepa", "autoresearch", "meta_harness", "best_of_n"}:
            raise ValueError("unsupported continuation_engine")
        if self.strategy not in {"gepa", "omni", "auto"}:
            raise ValueError("strategy must be one of: auto, gepa, omni")
        if self.max_evals is None and self.max_token_cost is None:
            raise ValueError("max_evals or max_token_cost is required")
        if self.max_evals is not None and (
            isinstance(self.max_evals, bool) or not isinstance(self.max_evals, int) or self.max_evals <= 0
        ):
            raise ValueError("max_evals must be a positive integer")
        if self.max_token_cost is not None and (
            isinstance(self.max_token_cost, bool)
            or not isinstance(self.max_token_cost, (int, float))
            or not math.isfinite(float(self.max_token_cost))
            or self.max_token_cost <= 0
        ):
            raise ValueError("max_token_cost must be a positive finite number")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        if self.sandbox is not True:
            raise ValueError("sandbox must be True; unsandboxed agent execution is not supported")
        if (self.input_cost_per_million is None) != (self.output_cost_per_million is None):
            raise ValueError("input/output pricing must be supplied together")
        if self.max_token_cost is not None and self.input_cost_per_million is None:
            raise ValueError("max_token_cost requires input/output pricing")
