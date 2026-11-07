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
        plan = plan_auto_allocation(4, None, baseline_evals=1)
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

    def test_explicit_endpoint_and_model_override_environment(self) -> None:
        with patch(
            "gepa_omni.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))],
        ):
            endpoint = resolve_endpoint(
                base_url="https://provider.example/v1",
                model="explicit-model",
                api_key="provider-secret",
                environ={
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    "OPENAI_MODEL": "environment-model",
                    "OPENAI_API_KEY": "must-not-be-used",
                },
            )
        self.assertEqual(endpoint.base_url, "https://provider.example/v1")
        self.assertEqual(endpoint.model, "explicit-model")
        self.assertEqual(endpoint.api_key, "provider-secret")

    def test_explicit_third_party_endpoint_uses_its_credential_policy(self) -> None:
        with (
            patch(
                "gepa_omni.security.socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))],
            ),
            self.assertRaisesRegex(EndpointPolicyError, "not permitted"),
        ):
            resolve_endpoint(
                base_url="https://provider.example/v1",
                model="explicit-model",
                environ={
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    "OPENAI_MODEL": "environment-model",
                    "OPENAI_API_KEY": "must-not-leave-openai",
                },
            )

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
                environ={},
            )
        with self.assertRaisesRegex(EndpointPolicyError, "embedded credentials"):
            resolve_endpoint(
                base_url="https://user:password@provider.example/v1",
                model="model",
                api_key="key",
                environ={},
            )

    def test_private_endpoint_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(EndpointPolicyError, "private"):
            resolve_endpoint(
                base_url="https://192.168.1.10/v1",
                model="model",
                api_key="key",
                environ={},
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

    def test_result_rejects_nonfinite_scores(self) -> None:
        for score in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "finite"):
                _result(SimpleNamespace(best_candidate="seed", best_score=score, total_evals=1), strategy="gepa")

    def test_endpoint_credentials_are_hidden_from_repr(self) -> None:
        endpoint = resolve_endpoint(base_url="http://localhost/v1", model="model", api_key="sensitive-key", environ={})
        self.assertNotIn("sensitive-key", repr(endpoint))

    def test_invalid_endpoint_ports_fail_policy(self) -> None:
        for url in ("https://example.com:bad", "https://example.com:99999", "https://[::1"):
            with self.subTest(url=url), self.assertRaises(EndpointPolicyError):
                resolve_endpoint(base_url=url, model="model", api_key="key", environ={})

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
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "openai-secret")

        self.assertEqual(captured["active_key"], "provider-secret")
        self.assertEqual(captured["kwargs"]["config"].engine_config["reflection"]["reflection_lm"], "provider-model")
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

        self.assertEqual(optimize_anything.call_count, 3)
        self.assertEqual(result.metadata["auto"]["pilot_engines"], ["gepa", "best_of_n"])
        self.assertEqual(result.metadata["auto"]["continuation_engine"], "gepa")
        self.assertEqual(result.total_evals, 7)

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


def test_auto_allocation_full_splits_and_many_engines():
    for size in (1, 6, 20):
        for count in (2, 3, 11, 25):
            plan = plan_auto_allocation(1000, 20, engines=tuple(str(i) for i in range(count)), selection_pool_size=size, baseline_evals=size)
            assert plan.pilot_evals >= size
            assert plan.continuation_evals >= size
            assert size + count * plan.pilot_evals + plan.continuation_evals == 1000
            assert count * plan.pilot_token_cost <= 10
            assert plan.continuation_token_cost >= 10
    assert plan_auto_allocation(100, 0.1).fallback_engine == "gepa"


def test_seed_score_uses_full_split_and_batch():
    from gepa_omni.api import _seed_score
    import pytest

    assert _seed_score("seed", {"evaluator": lambda _, x: x, "valset": [0, 1, 2]}) == 1
    assert _seed_score("seed", {"batch_evaluator": lambda pairs: [x for _, x in pairs], "valset": [0, 2]}) == 1
    assert _seed_score("seed", {"evaluator": lambda _: 0.25}) == 0.25
    with pytest.raises(ValueError, match="one score"):
        _seed_score("seed", {"batch_evaluator": lambda _: [], "valset": [1]})


def test_endpoint_bound_model_and_json_redaction():
    from gepa_omni.security import use_resolved_endpoint, redact_diagnostic
    import pytest

    endpoint = resolve_endpoint(base_url="http://localhost/v1", model="model", api_key="key", environ={})
    with use_resolved_endpoint(endpoint), pytest.raises(EndpointPolicyError):
        resolve_endpoint(model="")
    for text in ('{"api_key": "sensitive"}', "token=sensitive", "password: 'sensitive'"):
        assert "sensitive" not in redact_diagnostic(text)
    with pytest.raises(EndpointPolicyError):
        resolve_endpoint(base_url="https://100.100.100.200", model="model", api_key="key", environ={})


def test_unknown_standalone_options_fail_before_provider_call():
    import pytest
    with patch("gepa_omni.api._legacy_runner", return_value=None), pytest.raises(TypeError, match="typo"):
        optimize("seed", evaluator=lambda _: 1, config=OmniConfig(max_evals=1), typo=True)


def test_endpoint_environment_calls_serialize_and_restore(tmp_path):
    import threading
    entered, second_started, release = (threading.Event() for _ in range(3))
    values, errors = [], []

    def upstream(seed, **kwargs):
        if seed == "first":
            entered.set()
            assert release.wait(5)
        values.append((os.environ["OPENAI_MODEL"], os.environ["OPENAI_API_KEY"]))
        if seed == "second":
            raise ValueError("expected")
        return SimpleNamespace(best_candidate="winner", best_score=1.0, total_evals=1, metadata={})

    def call(name):
        if name == "second":
            second_started.set()
        try:
            optimize(name, evaluator=lambda _: 1.0, engine="gepa", api_key=name,
                     config=OmniConfig(max_evals=1, model=name, base_url="http://localhost/v1",
                                       run_dir=str(tmp_path / name / "run"), output_dir=str(tmp_path / name / "out")))
        except ValueError as exc:
            errors.append(str(exc))

    with patch.dict(os.environ, {}, clear=True), patch("gepa_omni.api._legacy_runner", return_value=None), patch("gepa.optimize_anything.optimize_anything", side_effect=upstream):
        before = dict(os.environ)
        first, other = (threading.Thread(target=call, args=(name,)) for name in ("first", "second"))
        first.start()
        try:
            assert entered.wait(5)
            other.start()
            assert second_started.wait(5)
            assert values == []
        finally:
            release.set()
            first.join(5)
            other.join(5)
        assert not first.is_alive() and not other.is_alive()
        assert values == [("first", "first"), ("second", "second")]
        assert errors == ["expected"]
        assert dict(os.environ) == before


def test_pinned_transports_keep_original_tls_hostname_and_numeric_peer():
    import io
    import urllib.request
    import httpx
    from gepa_omni.security import EndpointConfiguration
    from gepa_omni.transport import pinned_urlopen, pinned_openai_client
    from unittest.mock import MagicMock

    endpoint = EndpointConfiguration("https://provider.example/v1", "model", "key", "explicit", ("8.8.8.8",))
    peers = []
    names = []
    wire = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}'
    def connect(address, *args):
        peers.append(address)
        sock = MagicMock()
        sock.makefile.return_value = io.BytesIO(wire)
        return sock
    def tls(sock, *, server_hostname):
        names.append(server_hostname)
        return sock
    with patch("socket.create_connection", side_effect=connect), patch("ssl.SSLContext.wrap_socket", side_effect=tls), patch("socket.getaddrinfo", side_effect=AssertionError("DNS must not be repeated")):
        with pinned_urlopen(endpoint)(urllib.request.Request(endpoint.endpoint)) as response:
            assert response.read() == b"{}"
        with pinned_openai_client(endpoint) as client:
            response = client._client.send(httpx.Request("POST", endpoint.endpoint, content=b"{}"))
            assert response.status_code == 200
    assert peers == [("8.8.8.8", 443), ("8.8.8.8", 443)]
    assert names == ["provider.example", "provider.example"]


def test_auto_preserves_pilot_best_and_explicit_continuation(tmp_path):
    from gepa_omni.api import _standalone_auto
    heldout = []
    calls = []
    def run(seed, *, engine, config, **kwargs):
        calls.append((engine, seed, config.max_evals))
        score = {"gepa": 0.9, "best_of_n": 0.7}[engine] if len(calls) < 3 else 0.1
        return OptimizationResult(f"candidate-{score}", score, 2, {})
    task = {"valset": [0, 1], "test_set": ["heldout"], "evaluator": lambda candidate, example: heldout.append(candidate) or 0.5 if example == "heldout" else float(example)}
    with patch("gepa_omni.api._standalone_engine", side_effect=run):
        result = _standalone_auto("seed", task=task, config=OmniConfig(max_evals=40, model="model", continuation_engine="gepa", run_dir=str(tmp_path / "run"), output_dir=str(tmp_path / "out")), base_url="http://localhost/v1", api_key="key", api_key_env=None)
    assert result.best_candidate == "candidate-0.9"
    assert result.best_score == 0.9
    assert result.total_evals == 8
    assert calls[-1][0] == "gepa"
    assert heldout == ["candidate-0.9"]


def test_backend_aliases_do_not_override_replacement():
    from dataclasses import replace
    from native_omni.autoresearch import AutoResearchConfig
    from native_omni.best_of_n import BestOfNConfig
    from native_omni.meta_harness import MetaHarnessConfig
    import pytest
    for cls in (AutoResearchConfig, BestOfNConfig, MetaHarnessConfig):
        assert replace(cls(agent_backend="pi"), agent_backend="claude").agent_backend == "claude"
        with pytest.raises(ValueError, match="conflicts"):
            cls(agent_backend="pi", runner_profile="codex")
