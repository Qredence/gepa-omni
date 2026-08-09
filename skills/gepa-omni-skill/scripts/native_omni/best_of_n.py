"""Native independent Best-of-N candidate sampler."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    BudgetExhausted,
    TokenBudgetExceeded,
    extract_cost,
    make_runner,
    score_candidate,
    safe_name,
)
from .core import EvalServer, Result, Task


_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+.-]*)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class BestOfNConfig:
    model: str | None = None
    agent_backend: str = "codex"
    codex_command: str = "codex"
    pi_command: str = "pi"
    claude_command: str = "claude"
    codex_input_cost_per_million: float | None = None
    codex_output_cost_per_million: float | None = None
    timeout_seconds: float = 600.0
    max_n: int | None = None
    max_token_cost: float | None = None
    sandbox: bool = True
    run_dir: str | Path | None = None
    runner_factory: Any = None
    stop_at_score: float | None = None

    def __post_init__(self) -> None:
        self.agent_backend = str(self.agent_backend).strip().lower()
        if self.agent_backend not in {"codex", "pi", "claude"}:
            raise ValueError("best_of_n agent_backend must be one of: codex, pi, claude")
        self.timeout_seconds = float(self.timeout_seconds)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        if self.max_n is not None:
            self.max_n = int(self.max_n)
            if self.max_n <= 0:
                raise ValueError("max_n must be positive")


def _config(config: Any = None, **kwargs: Any) -> BestOfNConfig:
    values: dict[str, Any] = {
        "model": None,
        "agent_backend": "codex",
        "codex_command": "codex",
        "pi_command": "pi",
        "claude_command": "claude",
        "codex_input_cost_per_million": None,
        "codex_output_cost_per_million": None,
        "timeout_seconds": 600.0,
        "max_n": None,
        "max_token_cost": None,
        "sandbox": True,
        "run_dir": None,
        "runner_factory": None,
        "stop_at_score": None,
    }
    if isinstance(config, BestOfNConfig):
        values.update(vars(config))
    elif isinstance(config, dict):
        values.update(config)
    elif config is not None:
        if hasattr(config, "engine_config"):
            values.update(getattr(config, "engine_config") or {})
            for key in ("run_dir", "max_token_cost", "sandbox", "stop_at_score"):
                if hasattr(config, key):
                    values[key] = getattr(config, key)
        else:
            values.update(vars(config))
    values.update(kwargs)
    return BestOfNConfig(**{key: value for key, value in values.items() if key in BestOfNConfig.__dataclass_fields__})


def parse_candidate(text: str | None) -> str | None:
    """Parse one complete candidate from an agent response."""

    if not text:
        return None
    match = _FENCE_RE.search(text)
    if match:
        value = match.group(1).rstrip("\n")
        return value if value.strip() else None
    value = text.strip()
    return value or None


def _prompt(task: Task) -> str:
    sections: list[str] = []
    if task.objective:
        sections.append(f"## Objective\n{task.objective}")
    if task.background:
        sections.append(f"## Background\n{task.background}")
    if task.seed_candidate:
        sections.append(f"## Seed candidate\n```\n{task.seed_candidate}\n```")
    return (
        "You are generating one independent candidate solution.\n\n"
        + "\n\n".join(sections)
        + "\n\nRespond with the complete candidate in one fenced code block and no commentary."
    )


class BestOfNEngine:
    """Generate independent candidates and retain the highest-scoring one."""

    name = "best_of_n"

    def __init__(self, config: Any = None, **kwargs: Any) -> None:
        self.config = _config(config, **kwargs)

    def run(self, task: Task, server: EvalServer) -> Result:  # noqa: C901 - sample lifecycle owns budget accounting
        cfg = self.config
        if cfg.run_dir is None:
            raise ValueError("best_of_n requires an external run_dir")
        root = Path(cfg.run_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        prompt = _prompt(task)
        best_candidate = task.seed_candidate
        best_score = float("-inf")
        spent = 0.0
        samples = 0
        parse_failures = 0
        sample_log: list[dict[str, Any]] = []
        while True:
            if cfg.max_n is not None and samples >= cfg.max_n:
                break
            if server.budget.exhausted:
                break
            if cfg.max_token_cost is not None and spent >= cfg.max_token_cost:
                break
            samples += 1
            work_dir = root / f"sample-{samples:04d}-{safe_name(str(samples))}"
            work_dir.mkdir(parents=True, exist_ok=True)
            runner = make_runner(
                cfg.agent_backend,
                model=cfg.model,
                timeout_seconds=cfg.timeout_seconds,
                codex_command=cfg.codex_command,
                pi_command=cfg.pi_command,
                claude_command=cfg.claude_command,
                input_cost_per_million=cfg.codex_input_cost_per_million,
                output_cost_per_million=cfg.codex_output_cost_per_million,
                work_dir=work_dir,
                sandbox=cfg.sandbox,
                factory=cfg.runner_factory,
            )
            try:
                try:
                    # No session id and a new work directory are intentional:
                    # every sample is independent of all previous attempts.
                    agent_result = runner.run(
                        prompt,
                        work_dir=work_dir,
                        session_id=None,
                        max_token_cost=cfg.max_token_cost,
                        spent_token_cost=spent,
                    )
                except TokenBudgetExceeded:
                    samples -= 1
                    break
                except Exception as error:
                    sample_log.append({"sample": samples, "error": f"{type(error).__name__}: {error}"})
                    break
            finally:
                close = getattr(runner, "close", None)
                if callable(close):
                    close()
            cost = extract_cost(agent_result)
            if cost is not None:
                spent += cost
            candidate = parse_candidate(agent_result.final_text)
            if candidate is None:
                for filename in ("candidate.txt", "best_candidate.txt"):
                    candidate_path = work_dir / filename
                    if candidate_path.is_file():
                        candidate = candidate_path.read_text(encoding="utf-8")
                        break
            if not candidate:
                parse_failures += 1
                sample_log.append({"sample": samples, "parse_failed": True, "cost_usd": cost})
                if cost == 0:
                    break
                continue
            try:
                score, info = score_candidate(server, candidate)
            except BudgetExhausted:
                break
            row = {"sample": samples, "candidate_len": len(candidate), "score": score, "cost_usd": cost}
            sample_log.append(row)
            if score > best_score:
                best_candidate, best_score = candidate, score
            if cfg.stop_at_score is not None and best_score >= cfg.stop_at_score:
                break
            if cfg.max_token_cost is not None and cost == 0:
                break

        if best_score == float("-inf"):
            best_score = 0.0
        return Result(
            best_candidate,
            best_score,
            server.budget.used,
            server.eval_log,
            {
                "agent_backend": cfg.agent_backend,
                "work_dir": str(root),
                "adapter_cost": spent,
                "n_samples": samples,
                "n_parse_failures": parse_failures,
                "bon_cost_log": sample_log,
            },
        )


__all__ = ["BestOfNConfig", "BestOfNEngine", "parse_candidate"]
