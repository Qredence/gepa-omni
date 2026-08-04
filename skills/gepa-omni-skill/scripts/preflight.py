#!/usr/bin/env python3
"""Fail-fast checks for an engine-pluggable ``optimize_anything`` run.

Examples::

    python preflight.py --engine gepa
    python preflight.py --engine codex
    python preflight.py --engine autoresearch
    python preflight.py --engine meta_harness
    python preflight.py --engine omni
    GEPA_REFLECTION_LM=anthropic/claude-sonnet-4-6 python preflight.py --test-lm

The Codex proposer is an external adapter for the upstream ``gepa`` engine.
The Claude backend remains available for upstream compatibility. The plugin's
Claude-free profile uses ``--agent-backend pi`` and requires the maintained
GEPA fork's Pi runner extension.
"""

from __future__ import annotations

import argparse
import inspect
import os
import shutil
import sys

OK, BAD = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
problems: list[str] = []

ENGINE_CHOICES = ("gepa", "best_of_n", "autoresearch", "meta_harness", "codex", "omni")
BUILTIN_ENGINES = {"gepa", "best_of_n", "autoresearch", "meta_harness"}
COMPOSITION_HELPERS = (
    "optimize_best_of",
    "optimize_sequential",
    "optimize_parallel",
    "optimize_vote",
    "optimize_adaptive_sequential",
)
DEFAULT_LM_BY_ENGINE = {
    "gepa": "openai/gpt-5.1",
    "best_of_n": "claude-sonnet-4-6",
}


def check(label: str, ok: bool, fix: str = "") -> None:
    print(f"  [{OK if ok else BAD}] {label}")
    if not ok:
        problems.append(f"{label} — {fix}" if fix else label)


def _creds_for(lm: str) -> tuple[bool, str]:
    """Best-effort provider-credential check for a LiteLLM model id."""
    has_aws = bool(
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
    )
    if "bedrock" in lm:
        return (
            has_aws,
            "export AWS creds (AWS_BEARER_TOKEN_BEDROCK / AWS_ACCESS_KEY_ID / AWS_PROFILE)",
        )
    if lm.startswith("openai/") or lm.startswith("gpt-") or "gpt-5" in lm:
        return bool(os.environ.get("OPENAI_API_KEY")), "export OPENAI_API_KEY"
    if "claude" in lm or lm.startswith("anthropic/"):
        return bool(os.environ.get("ANTHROPIC_API_KEY")) or has_aws, (
            "export ANTHROPIC_API_KEY (or AWS creds)"
        )
    any_key = bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or has_aws
    )
    return any_key, "export your LiteLLM provider's API key"


def _check_gepa_import() -> tuple[bool, set[str]]:
    try:
        import gepa
        from gepa.optimize_anything import (  # noqa: F401
            OptimizeAnythingConfig,
            list_engines,
            optimize_adaptive_sequential,
            optimize_anything,
            optimize_best_of,
            optimize_parallel,
            optimize_sequential,
            optimize_vote,
        )

        check(
            f"import gepa ({getattr(gepa, '__version__', '?')}) + current launcher",
            True,
        )
        available = set(list_engines())
        missing = sorted(BUILTIN_ENGINES - available)
        check(
            "engine registry exposes GEPA, best_of_n, autoresearch, and meta_harness",
            not missing,
            "install the pinned maintained engine-capable `gepa[full]` fork; "
            f"missing: {', '.join(missing)}",
        )
        print(f"      engines -> {', '.join(sorted(available)) or '(none)'}")
        check(
            "current launcher exposes composition helpers: "
            + ", ".join(COMPOSITION_HELPERS),
            True,
        )
        return True, available
    except Exception as exc:
        check(
            "import current optimize_anything API",
            False,
            "install the pinned maintained engine-capable `gepa[full]` fork, with "
            "OptimizeAnythingConfig",
        )
        print(f"      {exc}")
        return False, set()


def _check_engine_available(engine: str, available: set[str]) -> None:
    required = "gepa" if engine in {"codex", "omni"} else engine
    check(
        f"requested engine `{required}` is available",
        required in available,
        "install the pinned maintained engine-capable `gepa[full]` fork, then rerun preflight",
    )


def _check_agent_tools(engine: str, backend: str, pi_command: str = "pi") -> None:
    required = [pi_command if backend == "pi" else "claude"]
    if engine in {"autoresearch", "meta_harness"}:
        required.extend(("jq", "curl"))
    for tool in required:
        executable = shutil.which(tool)
        check(
            f"`{tool}` on PATH (required by {engine}/{backend})",
            bool(executable),
            f"install and authenticate `{tool}` before a live {engine} run",
        )
        if executable:
            print(f"      {tool} -> {executable}")
    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        check(
            "`bwrap` on PATH (sandbox=True provides Pi OS confinement)"
            if backend == "pi"
            else "`bwrap` on PATH (default sandbox=True jails Claude)",
            bool(executable),
            (
                "install bubblewrap; Pi will not silently run unsandboxed"
                if backend == "pi"
                else "install bubblewrap, or explicitly configure sandbox=False"
            ),
        )
        if executable:
            print(f"      bwrap -> {executable}")
    elif backend == "pi":
        executable = shutil.which("sandbox-exec") or (
            "/usr/bin/sandbox-exec" if os.path.exists("/usr/bin/sandbox-exec") else None
        )
        check(
            "`sandbox-exec` on PATH (sandbox=True provides Pi OS confinement)",
            bool(executable),
            "macOS Seatbelt is required for sandboxed Pi runs; no unsandboxed fallback is used",
        )
        if executable:
            print(f"      sandbox-exec -> {executable}")


def _check_pi_surface(gepa_available: bool) -> None:
    if not gepa_available:
        return
    try:
        from gepa.oa.agent_runner import PiAgentRunner
        from gepa.oa.sandbox import pi_sandbox_prefix

        check(
            "GEPA fork exposes PiAgentRunner",
            callable(PiAgentRunner),
            "install the maintained GEPA fork",
        )
        check(
            "GEPA fork exposes pi_sandbox_prefix",
            callable(pi_sandbox_prefix),
            "install the maintained GEPA fork with Pi OS sandbox support",
        )
    except Exception as exc:
        check(
            "GEPA fork exposes the Pi agent-runner extension",
            False,
            (
                "install the maintained engine-capable GEPA fork; upstream PyPI "
                "releases without the extension are unsupported"
            ),
        )
        print(f"      {exc}")
    try:
        from pi_agent_proposer import PiAgentProposer

        constructor = inspect.signature(PiAgentProposer)
        call = inspect.signature(PiAgentProposer.__call__)
        check(
            "PiAgentProposer constructor contract",
            {"run_dir", "model", "timeout_seconds", "pi_command", "sandbox"}.issubset(
                constructor.parameters
            ),
            "refresh the plugin Pi proposer script",
        )
        check(
            "PiAgentProposer GEPA callable contract",
            {
                "candidate",
                "reflective_dataset",
                "components_to_update",
                "metadata",
            }.issubset(call.parameters),
            "refresh the plugin Pi proposer script",
        )
    except Exception as exc:
        check(
            "PiAgentProposer import and contract",
            False,
            "keep the skill scripts directory on PYTHONPATH",
        )
        print(f"      {exc}")


def _check_codex_compatibility(gepa_available: bool) -> None:
    cli = shutil.which("codex")
    check(
        "`codex` CLI on PATH (used by the GEPA proposer)",
        bool(cli),
        "install and authenticate the Codex CLI",
    )
    if cli:
        print(f"      codex -> {cli}")

    try:
        from codex_agent_proposer import CodexAgentProposer

        constructor = inspect.signature(CodexAgentProposer)
        call = inspect.signature(CodexAgentProposer.__call__)
        required_constructor = {"run_dir", "model", "timeout_seconds"}
        required_call = {"candidate", "reflective_dataset", "components_to_update"}
        constructor_ok = required_constructor.issubset(constructor.parameters)
        call_ok = (
            required_call.issubset(call.parameters) and "metadata" in call.parameters
        )
        check(
            "CodexAgentProposer constructor contract",
            constructor_ok,
            "refresh the plugin proposer script",
        )
        check(
            "CodexAgentProposer GEPA callable contract",
            call_ok,
            "refresh the plugin proposer script",
        )
    except Exception as exc:
        check(
            "CodexAgentProposer import and contract",
            False,
            "keep the skill scripts directory on PYTHONPATH",
        )
        print(f"      {exc}")

    if not gepa_available:
        return
    try:
        from gepa.optimize_anything import (
            OptimizeAnythingConfig,
            ReflectionConfig,
        )

        supports_hook = hasattr(ReflectionConfig, "custom_candidate_proposer")
        config_parameters = inspect.signature(OptimizeAnythingConfig).parameters
        supports_engine_config = {"engine", "engine_config"}.issubset(config_parameters)
        check(
            "installed GEPA exposes ReflectionConfig.custom_candidate_proposer",
            supports_hook,
            "upgrade the external gepa[full] dependency",
        )
        check(
            "OptimizeAnythingConfig exposes engine selection and engine_config",
            supports_engine_config,
            "upgrade the external gepa[full] dependency",
        )
    except Exception as exc:
        check(
            "GEPA proposer-hook compatibility",
            False,
            "upgrade the external gepa[full] dependency",
        )
        print(f"      {exc}")


def _check_lm_credentials(engine: str, backend: str = "claude") -> str:
    configured = os.environ.get("GEPA_REFLECTION_LM", "")
    effective = configured or DEFAULT_LM_BY_ENGINE[engine]
    if not configured and backend == "pi" and engine == "best_of_n":
        effective = "openai/gpt-5.1"
    if not configured:
        print(f"      GEPA_REFLECTION_LM unset -> engine default '{effective}'")
    ok, fix = _creds_for(effective)
    check(f"LLM creds present for '{effective}'", ok, fix)
    return effective


def _test_lm(target: str) -> None:
    try:
        from gepa.lm import LM

        output = LM(target)("Reply with the single word: ok")
        check(
            f"LM 1-call round-trip ({target})",
            bool(output),
            "LM returned empty; check model id / creds / region",
        )
    except Exception as exc:
        check(f"LM 1-call round-trip ({target})", False, str(exc)[:160])


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
    parser.add_argument(
        "--engine",
        default="gepa",
        choices=ENGINE_CHOICES,
    )
    parser.add_argument(
        "--test-lm", action="store_true", help="make an opt-in 1-call LM round-trip"
    )
    parser.add_argument(
        "--agent-backend",
        choices=("claude", "pi"),
        default="claude",
        help="backend for autoresearch/meta_harness; use pi for the Claude-free profile",
    )
    parser.add_argument(
        "--pi-command", default="pi", help="Pi executable used by the pi backend"
    )
    return parser.parse_args(argv)


def _check_selected_runtime(args: argparse.Namespace, gepa_available: bool) -> None:
    if args.engine == "codex":
        _check_codex_compatibility(gepa_available)
    elif args.engine in {"autoresearch", "meta_harness"}:
        _check_agent_tools(args.engine, args.agent_backend, args.pi_command)
        if args.agent_backend == "pi":
            _check_pi_surface(gepa_available)
    elif args.engine == "omni":
        _check_agent_tools("autoresearch", args.agent_backend, args.pi_command)
        _check_agent_tools("meta_harness", args.agent_backend, args.pi_command)
        if args.agent_backend == "pi":
            _check_pi_surface(gepa_available)
        if gepa_available:
            _check_lm_credentials("gepa", args.agent_backend)
            _check_lm_credentials("best_of_n", args.agent_backend)
    elif args.engine in {"gepa", "best_of_n"} and gepa_available:
        target = _check_lm_credentials(args.engine)
        if args.test_lm:
            _test_lm(target)


def main(argv: list[str] | None = None) -> int:
    problems.clear()
    args = _parse_args(argv)

    print("== optimize_anything preflight ==")
    gepa_available, available = _check_gepa_import()
    if gepa_available:
        _check_engine_available(args.engine, available)
    _check_selected_runtime(args, gepa_available)
    if args.test_lm and args.engine in {
        "codex",
        "autoresearch",
        "meta_harness",
        "omni",
    }:
        print(
            "      --test-lm only tests LiteLLM engines; use the separate live "
            "Codex/Claude smoke test for agent engines"
        )
    return _report()


if __name__ == "__main__":
    sys.exit(main())
