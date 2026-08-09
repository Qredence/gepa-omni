#!/usr/bin/env python3
"""Fail-fast checks for GEPA and the plugin-native Omni engines.

The published ``gepa==0.1.4`` package supplies the standalone reflective
engine. AutoResearch, Meta-Harness, Best-of-N, and the Omni composition use
the checked-in ``native_omni`` runtime. All model calls use the same
OpenAI-compatible Chat Completions endpoint configured by
``OPENAI_BASE_URL``, ``OPENAI_MODEL``, and ``OPENAI_API_KEY``. This command
does not make a model call unless ``--test-lm`` is explicitly requested.
"""

from __future__ import annotations

import argparse
import inspect
import math
import os
import sys

OK, BAD = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
problems: list[str] = []

ENGINE_CHOICES = ("gepa", "best_of_n", "autoresearch", "meta_harness", "codex", "omni")
NATIVE_ENGINES = {"best_of_n", "autoresearch", "meta_harness"}
PYPI_ENGINES = {"gepa", "codex", "omni"}


def check(label: str, ok: bool, fix: str = "") -> None:
    print(f"  [{OK if ok else BAD}] {label}")
    if not ok:
        problems.append(f"{label} — {fix}" if fix else label)


def _safe_detail(output: str, *, limit: int = 160) -> str:
    """Return bounded non-secret command diagnostics."""
    detail = " ".join(output.split())
    return detail[:limit]


def _check_chat_completions_config() -> bool:
    """Validate the shared provider boundary without printing the API key."""
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_configured = bool(base_url)
    model_configured = bool(model)
    key_configured = bool(api_key)
    check("OPENAI_BASE_URL is configured", base_configured, "export OPENAI_BASE_URL to an HTTP(S) API base URL")
    check("OPENAI_MODEL is configured", model_configured, "export OPENAI_MODEL with the deployed model name")
    check("OPENAI_API_KEY is configured", key_configured, "export OPENAI_API_KEY")
    valid_scheme = base_url.startswith(("http://", "https://"))
    check(
        "OPENAI_BASE_URL uses HTTP(S)",
        not base_url or valid_scheme,
        "set OPENAI_BASE_URL to an http:// or https:// URL",
    )
    return base_configured and model_configured and key_configured and valid_scheme


def _check_gepa_surface() -> bool:
    try:
        import gepa
        from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig, optimize_anything

        config_parameters = inspect.signature(GEPAConfig).parameters
        engine_parameters = inspect.signature(EngineConfig).parameters
        hook_available = hasattr(ReflectionConfig, "custom_candidate_proposer")
        api_ok = {"engine", "reflection"}.issubset(config_parameters) and "max_metric_calls" in engine_parameters
        check(
            f"import gepa ({getattr(gepa, '__version__', '?')}) published API",
            True,
            "install gepa[full]==0.1.4",
        )
        check(
            "GEPAConfig exposes nested EngineConfig and ReflectionConfig",
            api_ok,
            "install gepa[full]==0.1.4 and refresh the environment",
        )
        check(
            "ReflectionConfig exposes custom_candidate_proposer",
            hook_available,
            "install gepa[full]==0.1.4 and refresh the environment",
        )
        check("gepa.optimize_anything is callable", callable(optimize_anything), "refresh the GEPA installation")
        return api_ok and hook_available and callable(optimize_anything)
    except Exception as exc:
        check("import gepa published API", False, "install gepa[full]==0.1.4, then rerun preflight")
        print(f"      {_safe_detail(str(exc))}")
        return False


def _native_surface(engine: str) -> bool:
    try:
        import native_omni

        required = {
            "BudgetTracker",
            "EvalServer",
            "Task",
            "Result",
            "normalize_evaluator",
            "normalize_batch_evaluator",
        }
        missing = sorted(name for name in required if not hasattr(native_omni, name))
        check(
            f"plugin-native runtime exposes task/evaluation primitives for {engine}",
            not missing,
            f"restore native_omni exports: {', '.join(missing)}",
        )
        return not missing
    except Exception as exc:
        check(
            f"plugin-native runtime imports for {engine}",
            False,
            "keep the skill scripts directory on PYTHONPATH",
        )
        print(f"      {_safe_detail(str(exc))}")
        return False


def _check_native_runner(backend: str) -> bool:
    try:
        from native_omni import OpenAIChatCompletionRunner

        check(
            f"plugin-native {backend} uses the shared Chat Completions runner",
            callable(OpenAIChatCompletionRunner),
            "refresh the checked-in native_omni runtime",
        )
        return callable(OpenAIChatCompletionRunner)
    except Exception as exc:
        check(
            f"plugin-native {backend} runner is importable",
            False,
            "refresh the checked-in native_omni runtime",
        )
        print(f"      {_safe_detail(str(exc))}")
        return False


def _check_codex_proposer_surface() -> bool:
    try:
        from codex_agent_proposer import CodexAgentProposer

        constructor = inspect.signature(CodexAgentProposer)
        call = inspect.signature(CodexAgentProposer.__call__)
        constructor_ok = {"run_dir", "model", "timeout_seconds"}.issubset(constructor.parameters)
        call_ok = {"candidate", "reflective_dataset", "components_to_update", "metadata"}.issubset(call.parameters)
        check("CodexAgentProposer constructor contract", constructor_ok, "refresh the plugin proposer script")
        check("CodexAgentProposer callable contract", call_ok, "refresh the plugin proposer script")
        return constructor_ok and call_ok
    except Exception as exc:
        check(
            "CodexAgentProposer import and contract",
            False,
            "keep the skill scripts directory on PYTHONPATH",
        )
        print(f"      {_safe_detail(str(exc))}")
        return False


def _is_valid_pricing_value(value: object) -> bool:
    return (
        value is None
        or isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _check_chat_pricing(args: argparse.Namespace) -> None:
    values = (
        args.codex_input_cost_per_million,
        args.codex_output_cost_per_million,
        args.max_token_cost,
    )
    check(
        "Chat Completions pricing values are finite and non-negative",
        all(_is_valid_pricing_value(value) for value in values),
        "use finite non-negative USD values",
    )
    complete = args.codex_input_cost_per_million is not None and args.codex_output_cost_per_million is not None
    if any(value is not None for value in values):
        check(
            "Chat Completions input/output pricing is complete",
            complete,
            "provide both input and output pricing rates",
        )


def _check_agent_runtime(args: argparse.Namespace, engine: str) -> None:
    _check_chat_completions_config()
    _check_native_runner(args.agent_backend)
    _check_chat_pricing(args)


def _check_codex_proposer_runtime(args: argparse.Namespace, engine: str) -> None:
    _check_chat_completions_config()
    check("CodexAgentProposer uses the shared Chat Completions endpoint", True)
    _check_chat_pricing(args)


def _test_lm(target: str) -> None:
    try:
        from native_omni.chat_completions import OpenAIChatCompletionsClient

        result = OpenAIChatCompletionsClient().complete([{"role": "user", "content": "Reply with the single word: ok"}])
        check(
            f"Chat Completions 1-call round-trip ({target})",
            bool(result.final_text),
            "the endpoint returned empty content; check the model and credentials",
        )
    except Exception as exc:
        check(f"Chat Completions 1-call round-trip ({target})", False, _safe_detail(str(exc)))


def _report() -> int:
    print()
    if problems:
        print(f"\033[31m{len(problems)} blocker(s):\033[0m")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\033[32mAll preflight checks passed.\033[0m")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default="gepa", choices=ENGINE_CHOICES)
    parser.add_argument("--test-lm", action="store_true", help="make an opt-in 1-call Chat Completions round-trip")
    parser.add_argument(
        "--agent-backend",
        choices=("codex", "pi", "claude"),
        default="codex",
        help="backend for plugin-native agent engines (default: codex)",
    )
    parser.add_argument(
        "--model", default=None, help="legacy display-only model override; OPENAI_MODEL is authoritative"
    )
    parser.add_argument("--pi-command", default="pi", help="legacy compatibility option; no CLI is invoked")
    parser.add_argument("--codex-command", default="codex", help="legacy compatibility option; no CLI is invoked")
    parser.add_argument("--claude-command", default="claude", help="legacy compatibility option; no CLI is invoked")
    parser.add_argument("--codex-input-cost-per-million", type=float, default=None)
    parser.add_argument("--codex-output-cost-per-million", type=float, default=None)
    parser.add_argument("--max-token-cost", type=float, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    problems.clear()
    args = _parse_args(argv)
    print("== optimize_anything preflight ==")

    if args.engine in PYPI_ENGINES:
        gepa_ok = _check_gepa_surface()
        if args.engine == "codex":
            _check_codex_proposer_surface()
            _check_codex_proposer_runtime(args, args.engine)
        if args.engine == "gepa" and gepa_ok:
            _check_codex_proposer_surface()
            _check_codex_proposer_runtime(args, args.engine)

    if args.engine in NATIVE_ENGINES:
        _native_surface(args.engine)
        _check_agent_runtime(args, args.engine)

    if args.engine == "omni":
        _native_surface("omni")
        _check_agent_runtime(args, "omni")
        _check_codex_proposer_surface()

    if args.test_lm:
        target = os.environ.get("OPENAI_MODEL") or args.model or "<OPENAI_MODEL>"
        _test_lm(target)
    return _report()


if __name__ == "__main__":
    sys.exit(main())
