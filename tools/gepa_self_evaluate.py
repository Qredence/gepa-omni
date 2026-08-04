#!/usr/bin/env python3
"""Run a bounded, read-only GEPA self-evaluation for the Codex proposer.

The harness evaluates candidate proposer source in a temporary copy of the
plugin. It never writes candidates into the checkout. Run it from an
environment that has ``gepa[full]``, ``pytest``, ``ruff``, and the Codex CLI:

    uv run --with "gepa[full] @ git+https://github.com/gepa-ai/gepa.git" \
        python skills/gepa-omni-skill/scripts/self_evaluate.py \
        --model <codex-model> \
        --plugin-eval-command "node /path/to/plugin-eval.js"
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT
STAGED_PLUGIN_NAME = "gepa-omni"
SCRIPT_DIR = PLUGIN_ROOT / "skills" / "gepa-omni-skill" / "scripts"
PREFLIGHT = SCRIPT_DIR / "preflight.py"
TARGET_RELATIVE = Path("skills/gepa-omni-skill/scripts/codex_agent_proposer.py")
TEST_RELATIVE = Path("tests/test_codex_agent_proposer.py")
COMPONENT = "codex_agent_proposer_py"
DEFAULT_CHECK_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_METRIC_CALLS = 6
DEFAULT_MAX_CANDIDATE_PROPOSALS = 1
MAX_DIAGNOSTIC_CHARS = 2_000


if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


@dataclass(frozen=True)
class CommandResult:
    """Bounded subprocess output used in evaluator feedback."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class CandidateEvaluation:
    """A candidate score plus the diagnostics the proposer can learn from."""

    score: float
    valid: bool
    info: dict[str, Any]


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded(value: str, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"[truncated; last {limit} characters]\n{value[-limit:]}"


def _run_command(
    command: list[str], cwd: Path, timeout_seconds: float
) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            tuple(command),
            124,
            _text(exc.stdout),
            _text(exc.stderr),
            timed_out=True,
        )
    return CommandResult(
        tuple(command), completed.returncode, completed.stdout, completed.stderr
    )


def _uv_command(project: Path, *args: str) -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("`uv` was not found on PATH")
    return [uv, "run", "--project", str(project), *args]


def _plugin_eval_command(spec: str | None) -> list[str]:
    raw = spec or os.environ.get("PLUGIN_EVAL_COMMAND") or "plugin-eval"
    command = shlex.split(raw)
    if not command:
        raise ValueError("Plugin Eval command cannot be empty")
    executable = shutil.which(command[0])
    if executable:
        command[0] = executable
    else:
        local_executable = Path(command[0]).expanduser().resolve()
        if not local_executable.is_file():
            raise FileNotFoundError(
                "Plugin Eval executable was not found; pass "
                "--plugin-eval-command 'node /path/to/plugin-eval.js'"
            )
        command[0] = str(local_executable)
    return command


def _validate_run_dir(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("--run-dir must be outside the repository checkout")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _report_data(result: CommandResult) -> tuple[dict[str, Any], str]:
    if result.returncode != 0:
        return {}, _bounded(result.stderr or result.stdout)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {}, f"Plugin Eval returned invalid JSON: {exc}"
    if not isinstance(value, dict):
        return {}, "Plugin Eval returned a non-object JSON result"
    return value, ""


def _run_candidate_checks(
    fixture: Path,
    candidate_path: Path,
    plugin_eval_command: list[str],
    check_timeout_seconds: float,
) -> dict[str, CommandResult]:
    return {
        "tests": _run_command(
            _uv_command(fixture, "pytest", str(TEST_RELATIVE), "-q"),
            fixture,
            check_timeout_seconds,
        ),
        "ruff": _run_command(
            _uv_command(fixture, "ruff", "check", str(candidate_path)),
            fixture,
            check_timeout_seconds,
        ),
        "formatted": _run_command(
            _uv_command(fixture, "ruff", "format", "--check", str(candidate_path)),
            fixture,
            check_timeout_seconds,
        ),
        "analysis": _run_command(
            [
                *plugin_eval_command,
                "analyze",
                str(fixture),
                "--format",
                "json",
            ],
            fixture,
            check_timeout_seconds,
        ),
    }


def _parse_plugin_eval_result(
    result: CommandResult,
) -> tuple[float, list[Any], dict[str, Any], str]:
    report, analysis_error = _report_data(result)
    summary = report.get("summary", {})
    summary_error = ""
    if not isinstance(summary, dict):
        summary = {}
        summary_error = "Plugin Eval JSON summary must be an object"

    checks = report.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    warning_ids = [
        item.get("id")
        for item in checks
        if isinstance(item, dict) and item.get("status") == "warn"
    ]

    try:
        plugin_score = float(summary.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        plugin_score = 0.0
        summary_error = "Plugin Eval JSON summary.score must be numeric"
    if not math.isfinite(plugin_score):
        plugin_score = 0.0
        summary_error = "Plugin Eval JSON summary.score must be finite"

    return plugin_score, warning_ids, summary, analysis_error or summary_error


def _command_passed(result: CommandResult) -> bool:
    return result.returncode == 0 and not result.timed_out


def _evaluate_candidate_results(
    results: dict[str, CommandResult],
    example: dict[str, str] | None,
) -> CandidateEvaluation:
    plugin_score, warning_ids, summary, analysis_error = _parse_plugin_eval_result(
        results["analysis"]
    )
    passed = {name: _command_passed(result) for name, result in results.items()}
    valid = all(passed.values()) and not analysis_error
    info = {
        "score": plugin_score / 100.0 if valid else 0.0,
        "plugin_eval_score": plugin_score,
        "plugin_eval_warning_ids": warning_ids,
        "plugin_eval_summary": summary,
        "tests_passed": passed["tests"],
        "ruff_passed": passed["ruff"],
        "format_passed": passed["formatted"],
        "analysis_passed": passed["analysis"],
        "tests_output": _bounded(
            results["tests"].stdout + results["tests"].stderr
        ),
        "ruff_output": _bounded(results["ruff"].stdout + results["ruff"].stderr),
        "format_output": _bounded(
            results["formatted"].stdout + results["formatted"].stderr
        ),
        "analysis_output": _bounded(
            results["analysis"].stdout + results["analysis"].stderr
        ),
        "analysis_error": analysis_error,
        "example": example or {},
    }
    return CandidateEvaluation(float(info["score"]), valid, info)


def evaluate_candidate(
    candidate_text: str,
    *,
    plugin_eval_command: list[str],
    check_timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    example: dict[str, str] | None = None,
) -> CandidateEvaluation:
    """Score one proposer candidate in an isolated plugin fixture."""

    with tempfile.TemporaryDirectory(prefix="gepa-self-eval-") as temp_dir:
        fixture = Path(temp_dir) / STAGED_PLUGIN_NAME
        shutil.copytree(PLUGIN_ROOT, fixture)
        candidate_path = fixture / TARGET_RELATIVE
        candidate_path.write_text(candidate_text, encoding="utf-8")
        results = _run_candidate_checks(
            fixture,
            candidate_path,
            plugin_eval_command,
            check_timeout_seconds,
        )
        return _evaluate_candidate_results(results, example)


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", required=True, help="Explicit Codex model for proposals"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="Per-proposal Codex timeout (default: 600)",
    )
    parser.add_argument(
        "--check-timeout-seconds",
        type=float,
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        help="Timeout for fixture tests, Ruff, and Plugin Eval",
    )
    parser.add_argument(
        "--max-metric-calls",
        type=int,
        default=DEFAULT_MAX_METRIC_CALLS,
    )
    parser.add_argument(
        "--max-candidate-proposals",
        type=int,
        default=DEFAULT_MAX_CANDIDATE_PROPOSALS,
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="External directory for GEPA and proposer artifacts",
    )
    parser.add_argument(
        "--plugin-eval-command",
        help="Plugin Eval command, e.g. 'node /path/to/plugin-eval.js'",
    )
    return parser


def _positive(value: float | int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _proposal_errors(run_dir: Path) -> list[str]:
    proposal_root = run_dir / "proposer" / "proposals"
    return [
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(proposal_root.glob("*/error.txt"))
    ]


def _all_proposals_failed(run_dir: Path, *, seed: str, best: str) -> bool:
    proposal_dirs = sorted((run_dir / "proposer" / "proposals").glob("*/"))
    return (
        best == seed
        and bool(proposal_dirs)
        and all(
            (proposal_dir / "error.txt").is_file() for proposal_dir in proposal_dirs
        )
    )


def _write_best_artifacts(run_dir: Path, seed: str, best: str) -> None:
    (run_dir / "best_codex_agent_proposer.py").write_text(best, encoding="utf-8")
    diff = difflib.unified_diff(
        seed.splitlines(keepends=True),
        best.splitlines(keepends=True),
        fromfile="seed/codex_agent_proposer.py",
        tofile="best/codex_agent_proposer.py",
    )
    (run_dir / "best_codex_agent_proposer.diff").write_text(
        "".join(diff), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        _positive(args.timeout_seconds, "--timeout-seconds")
        _positive(args.check_timeout_seconds, "--check-timeout-seconds")
        _positive(args.max_metric_calls, "--max-metric-calls")
        _positive(args.max_candidate_proposals, "--max-candidate-proposals")
        plugin_eval_command = _plugin_eval_command(args.plugin_eval_command)
        run_dir = _validate_run_dir(
            args.run_dir or Path(tempfile.mkdtemp(prefix="gepa-self-evaluate-"))
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"self-evaluation configuration error: {exc}", file=sys.stderr)
        return 2

    preflight = _run_command(
        [sys.executable, str(PREFLIGHT), "--engine", "codex"],
        PLUGIN_ROOT,
        args.check_timeout_seconds,
    )
    if preflight.returncode != 0 or preflight.timed_out:
        print("GEPA/Codex preflight failed:", file=sys.stderr)
        print(preflight.stdout + preflight.stderr, file=sys.stderr)
        return 2

    seed_path = PLUGIN_ROOT / TARGET_RELATIVE
    seed = seed_path.read_text(encoding="utf-8")
    baseline = evaluate_candidate(
        seed,
        plugin_eval_command=plugin_eval_command,
        check_timeout_seconds=args.check_timeout_seconds,
        example={"phase": "baseline"},
    )
    _print_json(
        {
            "event": "baseline",
            "score": baseline.score,
            "valid": baseline.valid,
            "plugin_eval_score": baseline.info["plugin_eval_score"],
            "warning_ids": baseline.info["plugin_eval_warning_ids"],
            "run_dir": str(run_dir),
        }
    )
    if not baseline.valid:
        print("Baseline candidate failed one or more hard gates.", file=sys.stderr)
        return 2

    try:
        from codex_agent_proposer import CodexAgentProposer
        from gepa.optimize_anything import (
            EngineConfig,
            OptimizeAnythingConfig,
            ReflectionConfig,
            optimize_anything,
        )
    except ImportError as exc:
        print(
            "GEPA self-evaluation requires the engine-capable `gepa[full]`; "
            "run with `uv run --with "
            "'gepa[full] @ git+https://github.com/gepa-ai/gepa.git'`: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    proposer = CodexAgentProposer(
        run_dir=run_dir / "proposer",
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )

    def evaluate(
        candidate: dict[str, str], example: dict[str, str]
    ) -> tuple[float, dict[str, Any]]:
        candidate_text = candidate.get(COMPONENT)
        if not isinstance(candidate_text, str):
            return 0.0, {
                "error": f"candidate is missing string component {COMPONENT!r}"
            }
        record = evaluate_candidate(
            candidate_text,
            plugin_eval_command=plugin_eval_command,
            check_timeout_seconds=args.check_timeout_seconds,
            example=example,
        )
        return record.score, record.info

    try:
        result = optimize_anything(
            seed_candidate={COMPONENT: seed},
            evaluator=evaluate,
            dataset=[{"phase": "training"}],
            valset=[{"phase": "validation"}],
            test_set=[{"phase": "held-out"}],
            objective=(
                "Improve the proposer while preserving its public contract, "
                "tests, formatting, and read-only behavior."
            ),
            config=OptimizeAnythingConfig(
                engine="gepa",
                max_evals=args.max_metric_calls,
                max_concurrency=1,
                run_dir=str(run_dir / "gepa"),
                output_dir=str(run_dir / "gepa-output"),
                sandbox=True,
                engine_config={
                    "engine": EngineConfig(
                        seed=0,
                        max_candidate_proposals=args.max_candidate_proposals,
                        max_workers=1,
                        parallel=False,
                        cache_evaluation=True,
                    ),
                    "reflection": ReflectionConfig(
                        reflection_lm=None,
                        custom_candidate_proposer=proposer,
                        module_selector="all",
                        reflection_minibatch_size=1,
                    ),
                },
            ),
        )
    except Exception as exc:
        print(
            f"GEPA self-evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2

    best_candidate = result.best_candidate
    best = best_candidate.get(COMPONENT) if isinstance(best_candidate, dict) else None
    if not isinstance(best, str):
        print("GEPA returned no string best candidate.", file=sys.stderr)
        return 2
    best_record = evaluate_candidate(
        best,
        plugin_eval_command=plugin_eval_command,
        check_timeout_seconds=args.check_timeout_seconds,
        example={"phase": "final-best"},
    )
    _write_best_artifacts(run_dir, seed, best)
    errors = _proposal_errors(run_dir)
    unchanged_after_failure = _all_proposals_failed(run_dir, seed=seed, best=best)
    _print_json(
        {
            "event": "result",
            "best_changed": best != seed,
            "best_score": best_record.score,
            "best_valid": best_record.valid,
            "plugin_eval_score": best_record.info["plugin_eval_score"],
            "warning_ids": best_record.info["plugin_eval_warning_ids"],
            "engine": result.metadata.get("engine"),
            "engine_best_score": result.best_score,
            "total_evals": result.total_evals,
            "output_dir": result.metadata.get("output_dir"),
            "proposal_error_count": len(errors),
            "run_dir": str(run_dir),
        }
    )
    if unchanged_after_failure:
        print(
            "All proposal attempts failed; inspect proposer/*/error.txt under "
            f"{run_dir}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
