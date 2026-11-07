from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "plugins" / "fleet-optimize" / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import preflight  # noqa: E402


class FakeEngineConfig:
    def __init__(self, *, max_metric_calls: int | None = None, **_kwargs: object) -> None:
        self.max_metric_calls = max_metric_calls


class FakeReflectionConfig:
    custom_candidate_proposer = None


class FakeOptimizeAnythingConfig:
    def __init__(
        self,
        *,
        engine: str = "gepa",
        max_evals: int | None = None,
        max_concurrency: int = 8,
        engine_config: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        self.engine = engine
        self.max_evals = max_evals
        self.max_concurrency = max_concurrency
        self.engine_config = engine_config


def fake_gepa_modules() -> dict[str, ModuleType]:
    gepa = ModuleType("gepa")
    gepa.__version__ = "main-test"
    optimize = ModuleType("gepa.optimize_anything")
    optimize.OptimizeAnythingConfig = FakeOptimizeAnythingConfig

    def optimize_anything(*, batch_evaluator=None, **_kwargs: object) -> None:
        del batch_evaluator

    optimize.optimize_anything = optimize_anything
    strategies = ModuleType("gepa.strategies")
    sampling = ModuleType("gepa.strategies.proposal_sampling")
    selection = ModuleType("gepa.strategies.proposal_selection")
    sampling.PxNSampling = type("PxNSampling", (), {})
    sampling.SameParentSampling = type("SameParentSampling", (), {})
    sampling.IndependentSampling = type("IndependentSampling", (), {})
    selection.AllImprovements = type("AllImprovements", (), {})
    selection.BestImprovement = type("BestImprovement", (), {})
    selection.TopKImprovements = type("TopKImprovements", (), {})
    gepa.optimize_anything = optimize
    return {
        "gepa": gepa,
        "gepa.optimize_anything": optimize,
        "gepa.strategies": strategies,
        "gepa.strategies.proposal_sampling": sampling,
        "gepa.strategies.proposal_selection": selection,
    }


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {
            name: os.environ.get(name)
            for name in (
                "OPENAI_BASE_URL",
                "OPENAI_MODEL",
                "OPENAI_API_KEY",
                "GEPA_OMNI_API_KEY",
                "GEPA_OMNI_API_KEY_ENV",
                "PROVIDER_KEY",
            )
        }
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
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_MODEL": "test-model",
                "OPENAI_API_KEY": "test-key",
            }
        )

    def test_parser_defaults_to_pypi_gepa_and_codex(self) -> None:
        args = preflight._parse_args([])
        self.assertEqual(args.engine, "gepa")
        self.assertEqual(args.agent_backend, "codex")

    def test_gepa_latest_surface_checks_chat_completions_configuration(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "gepa"])

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("latest engine-pluggable API", text)
        self.assertIn("OPENAI_BASE_URL is configured", text)
        self.assertIn("shared Chat Completions endpoint", text)
        self.assertNotIn("CLI authentication", text)
        self.assertIn("All preflight checks passed", text)

    def test_missing_endpoint_configuration_does_not_claim_api_key_is_missing(self) -> None:
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 1)
        text = output.getvalue()
        self.assertIn("OPENAI_BASE_URL", text)
        self.assertIn("OPENAI_MODEL", text)
        self.assertNotIn("OPENAI_API_KEY", text)

    def test_missing_api_key_remediation_is_secret_safe(self) -> None:
        self._configure_api()
        secret = os.environ.pop("OPENAI_API_KEY")
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 1)
        text = output.getvalue()
        self.assertIn("environment or a secret manager", text)
        self.assertIn("do not provide it in chat", text)
        self.assertNotIn(secret, text)

    def test_preflight_is_non_interactive_when_configuration_is_complete(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with (
            patch.dict(sys.modules, fake_gepa_modules()),
            patch("builtins.input", side_effect=AssertionError),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 0)
        self.assertIn("All preflight checks passed", output.getvalue())

    def test_invalid_chat_completions_base_url_fails(self) -> None:
        self._configure_api()
        os.environ["OPENAI_BASE_URL"] = "llm.example/v1"
        output = io.StringIO()
        with redirect_stdout(output):
            result = preflight.main(["--engine", "meta_harness", "--agent-backend", "claude"])

        self.assertEqual(result, 1)
        self.assertIn("absolute http:// or https://", output.getvalue())

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

    def test_provider_specific_key_variable_is_checked_and_accepted(self) -> None:
        os.environ.update(
            {
                "OPENAI_BASE_URL": "https://provider.example/v1",
                "OPENAI_MODEL": "provider-model",
                "GEPA_OMNI_API_KEY_ENV": "PROVIDER_KEY",
                "PROVIDER_KEY": "provider-secret",
            }
        )
        output = io.StringIO()
        with patch.dict(sys.modules, fake_gepa_modules()), redirect_stdout(output):
            result = preflight.main(["--engine", "autoresearch"])

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("approved endpoint API key is configured", text)
        self.assertNotIn("provider-secret", text)

    def test_incomplete_chat_pricing_fails(self) -> None:
        self._configure_api()
        output = io.StringIO()
        with redirect_stdout(output):
            result = preflight.main(["--engine", "best_of_n", "--codex-input-cost-per-million", "1"])

        self.assertEqual(result, 1)
        self.assertIn("pricing is complete", output.getvalue())

    def test_old_or_missing_gepa_api_fails_with_latest_install_guidance(self) -> None:
        self._configure_api()
        old_gepa = ModuleType("gepa")
        old_optimize = ModuleType("gepa.optimize_anything")
        old_optimize.optimize_anything = lambda **_kwargs: None
        old_gepa.optimize_anything = old_optimize
        output = io.StringIO()
        with (
            patch.dict(sys.modules, {"gepa": old_gepa, "gepa.optimize_anything": old_optimize}),
            redirect_stdout(output),
        ):
            result = preflight.main(["--engine", "gepa"])

        self.assertEqual(result, 1)
        self.assertIn("latest compatible upstream GEPA", output.getvalue())

    def test_test_lm_is_opt_in_and_uses_configured_model_label(self) -> None:
        self._configure_api()
        with patch.object(preflight, "_test_lm") as test_lm:
            result = preflight.main(["--engine", "autoresearch", "--test-lm"])

        self.assertEqual(result, 0)
        test_lm.assert_called_once_with("test-model")


if __name__ == "__main__":
    unittest.main()


def test_preflight_endpoint_resolver_parity():
    cases = (
        {"OPENAI_BASE_URL": "HTTPS://api.openai.com/v1", "OPENAI_MODEL": "model", "GEPA_OMNI_API_KEY_ENV": "PROVIDER_KEY", "PROVIDER_KEY": "secret"},
        {"OPENAI_BASE_URL": "https://provider.example/v1", "OPENAI_MODEL": "model", "GEPA_OMNI_API_KEY_ENV": "MISSING", "GEPA_OMNI_API_KEY": "secret"},
    )
    for env in cases:
        with patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()):
            assert preflight._check_chat_completions_config()
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://[::1", "OPENAI_MODEL": "model"}, clear=True), redirect_stdout(io.StringIO()):
        assert not preflight._check_chat_completions_config()
