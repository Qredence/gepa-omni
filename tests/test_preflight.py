from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gepa-omni-skill"
    / "scripts"
)
REFERENCE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import preflight  # noqa: E402


class FakeConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeCodexRunner:
    def __init__(
        self,
        *,
        persistent: bool = False,
        sandbox: bool = True,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
    ) -> None:
        del persistent, sandbox, input_cost_per_million, output_cost_per_million


def fake_gepa_module(engines: list[str] | None = None) -> tuple[ModuleType, ModuleType]:
    gepa = ModuleType("gepa")
    optimize = ModuleType("gepa.optimize_anything")
    optimize.OptimizeAnythingConfig = FakeConfig
    optimize.optimize_anything = lambda **_kwargs: None
    optimize.list_engines = lambda: (
        engines
        or [
            "autoresearch",
            "best_of_n",
            "gepa",
            "meta_harness",
        ]
    )
    optimize.optimize_best_of = lambda *_args, **_kwargs: None
    optimize.optimize_sequential = lambda *_args, **_kwargs: None
    optimize.optimize_parallel = lambda *_args, **_kwargs: None
    optimize.optimize_vote = lambda *_args, **_kwargs: None
    optimize.optimize_adaptive_sequential = lambda *_args, **_kwargs: None
    gepa.optimize_anything = optimize
    return gepa, optimize


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gepa, self.optimize = fake_gepa_module()

    def _which(self, missing: set[str] | None = None):
        missing = missing or set()
        return lambda name: None if name in missing else f"/usr/bin/{name}"

    def _codex_modules(self, *, include_runner: bool = True) -> dict[str, ModuleType]:
        oa = ModuleType("gepa.oa")
        agent_runner = ModuleType("gepa.oa.agent_runner")
        if include_runner:
            agent_runner.CodexAgentRunner = FakeCodexRunner
        return {"gepa.oa": oa, "gepa.oa.agent_runner": agent_runner}

    def test_engine_capable_surface_passes_for_autoresearch(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize},
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test"}),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "autoresearch", "--agent-backend", "claude"])

        self.assertEqual(result, 0)
        self.assertIn("All preflight checks passed", output.getvalue())

    def test_codex_is_the_default_backend(self) -> None:
        self.assertEqual(preflight._parse_args([]).agent_backend, "codex")

    def test_codex_backend_checks_cli_fork_runner_and_pricing(self) -> None:
        output = io.StringIO()
        modules = {
            "gepa": self.gepa,
            "gepa.optimize_anything": self.optimize,
            **self._codex_modules(),
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(
                [
                    "--engine",
                    "autoresearch",
                    "--agent-backend",
                    "codex",
                    "--max-token-cost",
                    "1.0",
                    "--codex-input-cost-per-million",
                    "2.0",
                    "--codex-output-cost-per-million",
                    "8.0",
                ]
            )

        self.assertEqual(result, 0)
        self.assertIn("CodexAgentRunner", output.getvalue())
        self.assertIn("All preflight checks passed", output.getvalue())

    def test_codex_backend_reports_missing_cli(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize, **self._codex_modules()},
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which({"codex"})),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "codex"])

        self.assertEqual(result, 1)
        self.assertIn("codex", output.getvalue())

    def test_codex_backend_reports_missing_fork_runner(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {
                    "gepa": self.gepa,
                    "gepa.optimize_anything": self.optimize,
                    **self._codex_modules(include_runner=False),
                },
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "autoresearch", "--agent-backend", "codex"])

        self.assertEqual(result, 1)
        self.assertIn("Codex agent-runner extension", output.getvalue())

    def test_codex_backend_rejects_incomplete_pricing(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize, **self._codex_modules()},
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(
                ["--engine", "autoresearch", "--agent-backend", "codex", "--max-token-cost", "1.0"]
            )

        self.assertEqual(result, 1)
        self.assertIn("pricing is complete", output.getvalue())

    def test_missing_agent_runtime_is_actionable(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize},
            ),
            patch.object(
                preflight.shutil,
                "which",
                side_effect=self._which({"claude"}),
            ),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "claude"])

        self.assertEqual(result, 1)
        self.assertIn("claude", output.getvalue())
        self.assertIn("authenticate", output.getvalue())

    def test_pi_backend_checks_pi_and_fork_runner(self) -> None:
        oa = ModuleType("gepa.oa")
        agent_runner = ModuleType("gepa.oa.agent_runner")
        agent_runner.PiAgentRunner = object
        sandbox = ModuleType("gepa.oa.sandbox")
        sandbox.pi_sandbox_prefix = lambda _path: []
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {
                    "gepa": self.gepa,
                    "gepa.optimize_anything": self.optimize,
                    "gepa.oa": oa,
                    "gepa.oa.agent_runner": agent_runner,
                    "gepa.oa.sandbox": sandbox,
                },
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test"}),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "omni", "--agent-backend", "pi"])

        self.assertEqual(result, 0)
        self.assertIn("PiAgentRunner", output.getvalue())
        self.assertIn("sandbox-exec", output.getvalue())

    def test_pi_backend_fails_without_pi_or_os_sandbox(self) -> None:
        oa = ModuleType("gepa.oa")
        agent_runner = ModuleType("gepa.oa.agent_runner")
        agent_runner.PiAgentRunner = object
        sandbox = ModuleType("gepa.oa.sandbox")
        sandbox.pi_sandbox_prefix = lambda _path: []
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {
                    "gepa": self.gepa,
                    "gepa.optimize_anything": self.optimize,
                    "gepa.oa": oa,
                    "gepa.oa.agent_runner": agent_runner,
                    "gepa.oa.sandbox": sandbox,
                },
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which({"pi", "sandbox-exec"})),
            patch.object(preflight.os.path, "exists", return_value=False),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "pi"])

        self.assertEqual(result, 1)
        self.assertIn("pi", output.getvalue())
        self.assertIn("sandbox-exec", output.getvalue())

    def test_autoresearch_reports_missing_shell_tools(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize},
            ),
            patch.object(
                preflight.shutil,
                "which",
                side_effect=self._which({"jq", "curl"}),
            ),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 1)
        self.assertIn("jq", output.getvalue())
        self.assertIn("curl", output.getvalue())

    def test_linux_sandbox_reports_missing_bwrap(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize},
            ),
            patch.object(
                preflight.shutil,
                "which",
                side_effect=self._which({"bwrap"}),
            ),
            patch.object(preflight.sys, "platform", "linux"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "claude"])

        self.assertEqual(result, 1)
        self.assertIn("bwrap", output.getvalue())

    def test_old_package_fails_with_upgrade_guidance(self) -> None:
        old_gepa = ModuleType("gepa")
        old_optimize = ModuleType("gepa.optimize_anything")
        old_optimize.optimize_anything = lambda **_kwargs: None
        old_gepa.optimize_anything = old_optimize
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": old_gepa, "gepa.optimize_anything": old_optimize},
            ),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "gepa"])

        self.assertEqual(result, 1)
        self.assertIn("engine-capable", output.getvalue())
        self.assertIn("OptimizeAnythingConfig", output.getvalue())

    def test_missing_requested_engine_is_reported(self) -> None:
        gepa, optimize = fake_gepa_module(["gepa", "best_of_n"])
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": gepa, "gepa.optimize_anything": optimize},
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test"}),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 1)
        self.assertIn("requested engine `autoresearch` is available", output.getvalue())

    def test_omni_checks_composition_runtimes_and_lm_credentials(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                sys.modules,
                {"gepa": self.gepa, "gepa.optimize_anything": self.optimize},
            ),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test"},
            ),
            patch.object(preflight.shutil, "which", side_effect=self._which()),
            patch.object(preflight.sys, "platform", "darwin"),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "omni", "--agent-backend", "claude"])

        self.assertEqual(result, 0)
        self.assertIn("composition helpers", output.getvalue())

    def test_api_reference_contains_omni_engine_configuration(self) -> None:
        reference = (REFERENCE_ROOT / "references" / "api.md").read_text(
            encoding="utf-8"
        )
        for expected in (
            'engine="gepa"',
            'engine="autoresearch"',
            '`meta_harness`',
            "optimize_best_of",
            "optimize_adaptive_sequential",
        ):
            self.assertIn(expected, reference)

    def test_composition_config_covers_gepa_and_agent_engines(self) -> None:
        configs = [
            self.optimize.OptimizeAnythingConfig(engine="gepa", max_evals=2),
            self.optimize.OptimizeAnythingConfig(
                engine="autoresearch",
                max_evals=2,
                max_token_cost=0.10,
                engine_config={"model": "claude-sonnet-4-6"},
            ),
            self.optimize.OptimizeAnythingConfig(
                engine="meta_harness",
                max_evals=2,
                max_token_cost=0.10,
                engine_config={"model": "claude-sonnet-4-6"},
            ),
        ]
        captured: dict[str, object] = {}

        def fake_best_of(seed: str, **kwargs: object) -> None:
            captured["seed"] = seed
            captured.update(kwargs)

        self.optimize.optimize_best_of = fake_best_of
        self.optimize.optimize_best_of(
            "seed",
            evaluator=lambda candidate: (1.0, {"candidate": candidate}),
            configs=configs,
            objective="Improve the candidate.",
        )

        self.assertEqual(captured["seed"], "seed")
        self.assertIs(captured["configs"], configs)
        self.assertEqual(
            [config.kwargs["engine"] for config in configs],
            ["gepa", "autoresearch", "meta_harness"],
        )

    def test_claude_free_composition_config_uses_pi_for_both_agent_engines(self) -> None:
        configs = [
            self.optimize.OptimizeAnythingConfig(engine="gepa", max_evals=2),
            self.optimize.OptimizeAnythingConfig(
                engine="autoresearch",
                max_evals=2,
                max_token_cost=0.10,
                engine_config={"agent_backend": "pi", "model": "provider/model", "ralph": True},
            ),
            self.optimize.OptimizeAnythingConfig(
                engine="meta_harness",
                max_evals=2,
                max_token_cost=0.10,
                engine_config={"agent_backend": "pi", "model": "provider/model"},
            ),
        ]
        self.assertEqual(configs[1].kwargs["engine_config"]["agent_backend"], "pi")
        self.assertEqual(configs[2].kwargs["engine_config"]["agent_backend"], "pi")


if __name__ == "__main__":
    unittest.main()
