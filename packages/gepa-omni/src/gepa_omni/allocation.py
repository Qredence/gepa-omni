"""Deterministic budget planning for adaptive GEPA Omni runs."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Existing minimum proposer-cost budget for a useful portfolio.
MIN_AUTO_TOKEN_COST = 4.0


@dataclass(frozen=True)
class AllocationPlan:
    """Pilot and continuation budgets for ``strategy='auto'``."""

    engines: tuple[str, ...]
    pilot_evals: int | None
    pilot_token_cost: float | None
    continuation_evals: int | None
    continuation_token_cost: float | None
    fallback_engine: str | None = None


def plan_auto_allocation(
    max_evals: int | None,
    max_token_cost: float | None,
    *,
    engines: tuple[str, ...] = ("gepa", "autoresearch", "meta_harness"),
    selection_pool_size: int = 1,
    baseline_evals: int = 0,
) -> AllocationPlan:
    """Reserve pilots while keeping the majority of budget for continuation.

    A portfolio needs at least one pilot evaluation per engine and one
    continuation evaluation. Smaller budgets therefore fall back to GEPA.
    """

    if not engines or selection_pool_size < 1 or baseline_evals < 0:
        raise ValueError("engines and selection pool must be nonempty; baseline cost cannot be negative")
    if max_token_cost is not None and (not math.isfinite(max_token_cost) or max_token_cost <= 0):
        raise ValueError("max_token_cost must be positive and finite")
    available = None if max_evals is None else max_evals - baseline_evals
    if (
        (available is not None and available < (len(engines) + 1) * selection_pool_size)
        or (max_token_cost is not None and max_token_cost < MIN_AUTO_TOKEN_COST)
        or (available is None and max_token_cost is None)
    ):
        return AllocationPlan((), None, None, max_evals, max_token_cost, fallback_engine="gepa")
    pilot_evals = (
        None
        if available is None
        else min(
            max(selection_pool_size, min(5, available // 10)),
            (available - selection_pool_size) // len(engines),
        )
    )
    continuation_evals = None if available is None else available - len(engines) * pilot_evals
    pilot_cost = None if max_token_cost is None else max_token_cost * min(0.1, 0.5 / len(engines))
    continuation_cost = None if max_token_cost is None else max_token_cost - len(engines) * pilot_cost
    return AllocationPlan(
        engines=engines,
        pilot_evals=pilot_evals,
        pilot_token_cost=pilot_cost,
        continuation_evals=continuation_evals,
        continuation_token_cost=continuation_cost,
    )


__all__ = ["AllocationPlan", "plan_auto_allocation"]
