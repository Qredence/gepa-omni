"""Deterministic budget planning for adaptive GEPA Omni runs."""

from __future__ import annotations

from dataclasses import dataclass


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
) -> AllocationPlan:
    """Reserve pilots while keeping the majority of budget for continuation.

    A portfolio needs at least one pilot evaluation per engine and one
    continuation evaluation. Smaller budgets therefore fall back to GEPA.
    """

    if max_evals is not None and max_evals < len(engines) + 2:
        return AllocationPlan((), None, None, max_evals, max_token_cost, fallback_engine="gepa")
    if max_evals is None and (max_token_cost is None or max_token_cost < 4.0):
        return AllocationPlan((), None, None, max_evals, max_token_cost, fallback_engine="gepa")
    pilot_evals = None if max_evals is None else max(1, min(5, max_evals // 10))
    continuation_evals = None if max_evals is None else max(1, max_evals - len(engines) * pilot_evals)
    pilot_cost = None if max_token_cost is None else max_token_cost * 0.1
    continuation_cost = None if max_token_cost is None else max_token_cost - len(engines) * pilot_cost
    return AllocationPlan(
        engines=engines,
        pilot_evals=pilot_evals,
        pilot_token_cost=pilot_cost,
        continuation_evals=continuation_evals,
        continuation_token_cost=continuation_cost,
    )


__all__ = ["AllocationPlan", "plan_auto_allocation"]
