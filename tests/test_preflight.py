from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import preflight  # noqa: E402


class FakeEngineConfig:
    def __init__(self, *, max_metric_calls: int | None = None, **_kwargs: object) -> None:
        self.max_metric_calls = max_metric_calls


class FakeReflectionConfig:
    custom_candidate_proposer = None


class FakeGEPAConfig:
    def __init__(
        self,
        *,
        engine: FakeEngineConfig | None = None,
        reflection: FakeReflectionConfig | None = None,
        **_kwargs: object,
    ) -> None:
        self.engine = engine
        self.reflection = reflection


def fake_gepa_modules() -> dict[str, ModuleType]:
    gepa = ModuleType("gepa")
    gepa.__version__ = "0.1.4"
    optimize = ModuleType("gepa.optimize_anything")
    optimize.GEPAConfig = FakeGEPAConfig
    optimize.EngineConfig = FakeEngineConfig
    optimize.ReflectionConfig = FakeReflectionConfig
    optimize.optimize_anything = lambda **_kwargs: None
    gepa.optimize_anything = optimize
    return {"gepa": gepa, "gepa.optimize_anything": optimize}


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {name: os.environ.get(name) for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")}
        for name in self._saved_env:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @staticmethod
    def _configure_api() -> None:
        os.environ.update(
            {
                "OPENAI_BASE_URL": "https://llm.example/v1",
                "OPENAI_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
            }
        )

    def test_parser_defaults_to_pypi_gepa_and_codex(self) -> None:
        args = preflight._parse_args([])
        self.assertEqual(args.engine, "gepa")
        self.assertEqual(args.agent_backend, "codex")

    def test_gepa_pypi_surface_checks_chat_completions_configuration(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "gepa"])

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("published API", text)
        self.assertIn("OPENAI_BASE_URL is configured", text)
        self.assertIn("shared Chat Completions endpoint", text)
        self.assertNotIn("CLI authentication", text)
        self.assertIn("All preflight checks passed", text)

    def test_missing_chat_completions_configuration_fails_with_variable_names(self) -> None:
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 1)
        text = output.getvalue()
        self.assertIn("OPENAI_BASE_URL", text)
        self.assertIn("OPENAI_MODEL", text)
        self.assertIn("OPENAI_API_KEY", text)

    def test_invalid_chat_completions_base_url_fails(self) -> None:
        self._configure_api()
        os.environ["OPENAI_BASE_URL"] = "llm.example/v1"
        output = io.StringIO()
        with redirect_stdout(output):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "claude"])

        self.assertEqual(result, 1)
        self.assertIn("uses HTTP(S)", output.getvalue())

    def test_native_backends_share_one_runner_and_do_not_probe_cli_tools(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with redirect_stdout(output):
            result = preflight.main(["--engine", "autoresearch", "--agent-backend", "pi"])

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("shared Chat Completions runner", text)
        self.assertNotIn("jq", text)
        self.assertNotIn("curl", text)
        self.assertNotIn("sandbox-exec", text)
        self.assertNotIn("CLI", text)

    def test_incomplete_chat_pricing_fails(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with redirect_stdout(output):
            result = preflight.main(
                ["--engine", "best_of_n", "--codex-input-cost-per-million", "1"]
            )

        self.assertEqual(result, 1)
        self.assertIn("pricing is complete", output.getvalue())

    def test_old_or_missing_pypi_api_fails_with_install_guidance(self) -> None:
        self._configure_api()
        old_gepa = ModuleType("gepa")
        old_optimize = ModuleType("gepa.optimize_anything")
        old_optimize.optimize_anything = lambda **_kwargs: None
        old_gepa.optimize_anything = old_optimize
        output = io.StringIO()
        with patch.dict(sys.modules, {"gepa": old_gepa, "gepa.optimize_anything": old_optimize}), redirect_stdout(output):
            result = preflight.main(["--engine", "gepa"])

        self.assertEqual(result, 1)
        self.assertIn("gepa[full]==0.1.4", output.getvalue())

    def test_test_lm_is_opt_in_and_uses_configured_model_label(self) -> None:
        self._configure_api()
        with patch.object(preflight, "_test_lm") as test_lm:
            result = preflight.main(["--engine", "autoresearch", "--test-lm"])

        self.assertEqual(result, 0)
        test_lm.assert_called_once_with("test-model")


if __name__ == "__main__":
    unittest.main()
