from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gepa_omni import OmniConfig, EndpointPolicyError, resolve_endpoint
from gepa_omni.allocation import plan_auto_allocation
from gepa_omni.api import OptimizationResult, _result, optimize


class GePAOmniPackageTests(unittest.TestCase):
    def test_public_symbols_and_default_strategy_are_stable(self) -> None:
        self.assertTrue(callable(optimize))
        self.assertEqual(OmniConfig(max_evals=10).strategy, "auto")

    def test_auto_allocation_falls_back_for_small_budgets(self) -> None:
        plan = plan_auto_allocation(4, None)
        self.assertEqual(plan.fallback_engine, "gepa")
        self.assertEqual(plan.continuation_evals, 4)

    def test_auto_allocation_reserves_pilots_and_continuation(self) -> None:
        plan = plan_auto_allocation(40, 20.0)
        self.assertEqual(plan.engines, ("gepa", "autoresearch", "meta_harness"))
        self.assertEqual(plan.pilot_evals, 4)
        self.assertEqual(plan.continuation_evals, 28)
        self.assertAlmostEqual(plan.pilot_token_cost or 0.0, 2.0)
        self.assertAlmostEqual(plan.continuation_token_cost or 0.0, 14.0)

    def test_openai_key_is_not_selected_for_third_party_endpoint(self) -> None:
        with self.assertRaisesRegex(EndpointPolicyError, "not permitted"):
            resolve_endpoint(
                base_url="https://provider.example/v1",
                model="model",
                environ={"OPENAI_API_KEY": "must-not-leave-openai"},
            )

    def test_provider_key_is_selected_for_third_party_endpoint(self) -> None:
        endpoint = resolve_endpoint(
            base_url="https://provider.example/v1",
            model="model",
            environ={"GEPA_OMNI_API_KEY": "provider-secret"},
        )
        self.assertEqual(endpoint.api_key, "provider-secret")
        self.assertEqual(endpoint.key_source, "GEPA_OMNI_API_KEY")

    def test_openai_key_alias_cannot_be_selected_for_third_party_endpoint(self) -> None:
        with self.assertRaisesRegex(EndpointPolicyError, "not permitted"):
            resolve_endpoint(
                base_url="https://provider.example/v1",
                model="model",
                api_key_env="OPENAI_API_KEY",
                environ={"OPENAI_API_KEY": "must-not-leave-openai"},
            )

    def test_remote_http_and_embedded_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(EndpointPolicyError, "HTTPS"):
            resolve_endpoint(
                base_url="http://provider.example/v1",
                model="model",
                api_key="key",
            )
        with self.assertRaisesRegex(EndpointPolicyError, "embedded credentials"):
            resolve_endpoint(
                base_url="https://user:password@provider.example/v1",
                model="model",
                api_key="key",
            )

    def test_private_endpoint_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(EndpointPolicyError, "private"):
            resolve_endpoint(
                base_url="https://192.168.1.10/v1",
                model="model",
                api_key="key",
            )
        endpoint = resolve_endpoint(
            base_url="https://192.168.1.10/v1",
            model="model",
            api_key="key",
            environ={"GEPA_OMNI_ALLOW_PRIVATE_ENDPOINTS": "1"},
        )
        self.assertEqual(endpoint.base_url, "https://192.168.1.10/v1")

    def test_unresolved_hostname_is_rejected(self) -> None:
        with patch(
            "gepa_omni.security.socket.getaddrinfo",
            side_effect=socket.gaierror("name or service not known"),
        ):
            with self.assertRaisesRegex(EndpointPolicyError, "could not be resolved"):
                resolve_endpoint(
                    base_url="https://provider.example/v1",
                    model="model",
                    api_key="key",
                )

    def test_hostname_resolving_to_private_address_is_rejected(self) -> None:
        with patch(
            "gepa_omni.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.50.1", 0))],
        ):
            with self.assertRaisesRegex(EndpointPolicyError, "private"):
                resolve_endpoint(
                    base_url="https://safe-looking.example/v1",
                    model="model",
                    api_key="key",
                )

    def test_result_without_score_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not include a score"):
            _result(SimpleNamespace(best_candidate="seed", metadata={}), strategy="gepa")

    def test_public_optimize_delegates_to_stable_task_contract(self) -> None:
        class FakeRaw:
            best_candidate = "winner"
            best_score = 0.9
            total_evals = 3
            metadata = {"engine": "gepa"}

        calls: list[dict[str, object]] = []

        def fake_runner(seed: str, **kwargs: object) -> FakeRaw:
            calls.append({"seed": seed, **kwargs})
            return FakeRaw()

        with patch("gepa_omni.api._legacy_runner", return_value=fake_runner):
            result = optimize(
                "seed",
                evaluator=lambda _candidate, _example: (1.0, {}),
                dataset=["train"],
                valset=["validation"],
                test_set=["heldout"],
                config=OmniConfig(max_evals=8),
            )

        self.assertIsInstance(result, OptimizationResult)
        self.assertEqual(result.best_candidate, "winner")
        self.assertEqual(calls[0]["strategy"], "auto")
        task = calls[0]["task"]
        self.assertEqual(task["test_set"], ["heldout"])

    def test_standalone_run_scores_heldout_set_and_restores_endpoint_environment(self) -> None:
        class FakeRaw:
            best_candidate = "winner"
            best_score = 0.9
            total_evals = 2
            metadata = {}

        captured: dict[str, object] = {}

        def fake_optimize(seed: str, **kwargs: object) -> FakeRaw:
            captured["seed"] = seed
            captured["kwargs"] = kwargs
            captured["active_key"] = os.environ.get("OPENAI_API_KEY")
            return FakeRaw()

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(
                os.environ,
                {
                    "OPENAI_BASE_URL": "https://provider.example/v1",
                    "OPENAI_MODEL": "provider-model",
                    "OPENAI_API_KEY": "openai-secret",
                    "GEPA_OMNI_API_KEY": "provider-secret",
                },
                clear=True,
            ),
            patch("gepa_omni.api._legacy_runner", return_value=None),
            patch("gepa.optimize_anything.optimize_anything", side_effect=fake_optimize),
        ):
            result = optimize(
                "seed",
                evaluator=lambda _candidate, example: (1.0 if example == "good" else 0.5, {}),
                test_set=["good", "bad"],
                config=OmniConfig(
                    strategy="gepa",
                    max_evals=2,
                    run_dir=str(Path(temp_dir) / "runs"),
                    output_dir=str(Path(temp_dir) / "outputs"),
                ),
            )

        self.assertEqual(captured["active_key"], "provider-secret")
        self.assertEqual(captured["kwargs"]["config"].engine_config["reflection"]["reflection_lm"], "provider-model")
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), None)
        self.assertEqual(result.metadata["heldout_scores"], [1.0, 0.5])
        self.assertEqual(result.metadata["heldout_score"], 0.75)
        self.assertEqual(result.metadata["test_score"], 0.75)
        self.assertNotIn("test_set", captured["kwargs"])

    def test_standalone_run_rejects_openai_key_for_arbitrary_endpoint(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(
                os.environ,
                {
                    "OPENAI_BASE_URL": "https://provider.example/v1",
                    "OPENAI_MODEL": "provider-model",
                    "OPENAI_API_KEY": "openai-secret",
                },
                clear=True,
            ),
            patch("gepa_omni.api._legacy_runner", return_value=None),
            patch("gepa.optimize_anything.optimize_anything") as optimize_anything,
        ):
            with self.assertRaisesRegex(EndpointPolicyError, "not permitted"):
                optimize(
                    "seed",
                    evaluator=lambda _candidate, _example: (1.0, {}),
                    config=OmniConfig(
                        strategy="gepa",
                        max_evals=2,
                        run_dir=str(Path(temp_dir) / "runs"),
                        output_dir=str(Path(temp_dir) / "outputs"),
                    ),
                )
        optimize_anything.assert_not_called()

    def test_standalone_run_rejects_checkout_artifacts(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(
                os.environ,
                {
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    "OPENAI_MODEL": "model",
                    "OPENAI_API_KEY": "key",
                },
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "outside the development checkout"):
                optimize(
                    "seed",
                    evaluator=lambda _candidate, _example: (1.0, {}),
                    config=OmniConfig(
                        strategy="gepa",
                        max_evals=2,
                        run_dir=str(Path(__file__).resolve().parents[1] / "runs"),
                        output_dir=str(Path(temp_dir) / "outputs"),
                    ),
                )

    def test_clean_package_auto_executes_adaptive_plan_without_plugin_runtime(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(
                os.environ,
                {
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    "OPENAI_MODEL": "model",
                    "OPENAI_API_KEY": "key",
                },
                clear=True,
            ),
            patch("gepa_omni.api._legacy_runner", return_value=None),
            patch("gepa.optimize_anything.optimize_anything") as optimize_anything,
        ):
            optimize_anything.return_value = type(
                "FakeRaw",
                (),
                {"best_candidate": "winner", "best_score": 0.9, "total_evals": 2, "metadata": {}},
            )()
            result = optimize(
                "seed",
                evaluator=lambda _candidate, _example: (1.0, {}),
                valset=["validation"],
                config=OmniConfig(
                    strategy="auto",
                    max_evals=40,
                    run_dir=str(Path(temp_dir) / "runs"),
                    output_dir=str(Path(temp_dir) / "outputs"),
                ),
            )

        self.assertEqual(optimize_anything.call_count, 4)
        self.assertEqual(result.metadata["auto"]["pilot_engines"], ["gepa", "autoresearch", "meta_harness"])
        self.assertEqual(result.metadata["auto"]["continuation_engine"], "gepa")
        self.assertEqual(result.total_evals, 8)

    def test_explicit_gepa_strategy_selects_the_standalone_engine(self) -> None:
        class FakeRaw:
            best_candidate = "winner"
            best_score = 0.9
            total_evals = 1
            metadata = {}

        calls: list[dict[str, object]] = []

        def fake_runner(seed: str, **kwargs: object) -> FakeRaw:
            calls.append({"seed": seed, **kwargs})
            return FakeRaw()

        with patch("gepa_omni.api._legacy_runner", return_value=fake_runner):
            optimize(
                "seed",
                evaluator=lambda _candidate, _example: (1.0, {}),
                config=OmniConfig(strategy="gepa", max_evals=8),
            )

        self.assertEqual(calls[0]["engine"], "gepa")

    def test_explicit_endpoint_is_visible_to_the_plugin_runtime(self) -> None:
        class FakeRaw:
            best_candidate = "winner"
            best_score = 0.9
            total_evals = 1
            metadata = {}

        seen: list[object] = []

        def fake_runner(seed: str, **_kwargs: object) -> FakeRaw:
            del seed
            seen.append(resolve_endpoint())
            return FakeRaw()

        with patch.dict(os.environ, {}, clear=True), patch("gepa_omni.api._legacy_runner", return_value=fake_runner):
            optimize(
                "seed",
                evaluator=lambda _candidate, _example: (1.0, {}),
                api_key="audit-key",
                config=OmniConfig(
                    strategy="gepa",
                    max_evals=2,
                    base_url="https://provider.example/v1",
                    model="audit-model",
                ),
            )

        self.assertEqual(seen[0].base_url, "https://provider.example/v1")
        self.assertEqual(seen[0].model, "audit-model")
        self.assertEqual(seen[0].api_key, "audit-key")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EndpointPolicyError):
                resolve_endpoint()

    def test_chat_completions_url_preserves_the_query_string(self) -> None:
        endpoint = resolve_endpoint(
            base_url="https://provider.example/v1?api-version=2024-10-21",
            model="provider-model",
            api_key="provider-key",
        )

        self.assertEqual(endpoint.endpoint, "https://provider.example/v1/chat/completions?api-version=2024-10-21")


if __name__ == "__main__":
    unittest.main()
