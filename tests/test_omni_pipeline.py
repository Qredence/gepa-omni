from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
_SCRIPT_DIR_STR = str(SCRIPT_DIR)
sys.path.insert(0, _SCRIPT_DIR_STR)
try:
    import omni_pipeline  # noqa: E402
    from native_omni.runners import AgentRunResult  # noqa: E402
finally:
    if sys.path and sys.path[0] == _SCRIPT_DIR_STR:
        sys.path.pop(0)
    elif _SCRIPT_DIR_STR in sys.path:
        sys.path.remove(_SCRIPT_DIR_STR)


class FakeConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeLauncher:
    OptimizeAnythingConfig = FakeConfig

    def __init__(self) -> None:
        self.best_of_calls: list[tuple[str, dict[str, object]]] = []
        self.optimize_calls: list[tuple[str, dict[str, object]]] = []

    def optimize_best_of(self, seed: str, **kwargs: object) -> SimpleNamespace:
        self.best_of_calls.append((seed, kwargs))
        return SimpleNamespace(best_candidate="phase-1-winner", metadata={})

    def optimize_anything(self, seed: str, **kwargs: object) -> SimpleNamespace:
        self.optimize_calls.append((seed, kwargs))
        return SimpleNamespace(best_candidate="final-candidate", metadata={})


class PublicResultLauncher:
    def __init__(self, config_cls: type[object]) -> None:
        self.GEPAConfig = config_cls
        self.kwargs: dict[str, object] = {}
        self.optimize_calls = 0
        self.best_of_calls = 0

    def optimize_best_of(self, _seed: str, **_kwargs: object) -> SimpleNamespace:
        self.best_of_calls += 1
        return SimpleNamespace(best_candidate="unexpected")

    def optimize_anything(self, seed: str, **kwargs: object) -> SimpleNamespace:
        self.optimize_calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(
            best_candidate={"current_candidate": seed},
            best_idx=0,
            val_aggregate_scores=[0.75],
            total_metric_calls=1,
            run_dir="/external/runs/public-gepa",
        )


class OmniPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = FakeLauncher()
        self.proposer_dirs: list[Path] = []
        self.proposer_calls: list[dict[str, object]] = []

        def proposer_factory(**kwargs: object) -> object:
            self.proposer_calls.append(dict(kwargs))
            self.proposer_dirs.append(kwargs["run_dir"])  # type: ignore[arg-type]
            return object()

        self.proposer_factory = proposer_factory
        self.evaluator = object()
        self.task = {
            "evaluator": self.evaluator,
            "objective": "Improve the candidate.",
            "dataset": ["train"],
            "valset": ["validation"],
            "test_set": ["held-out"],
            "config": "ignored because config is constructed per phase",
        }

    def _run_omni(self, **overrides: object) -> SimpleNamespace:
        arguments: dict[str, object] = {
            "task": self.task,
            "max_evals": 40,
            "max_token_cost": 20.0,
            "run_dir": "/external/runs/omni",
            "output_dir": "/external/outputs/omni",
            "codex_input_cost_per_million": 2.0,
            "codex_output_cost_per_million": 8.0,
            "launcher": self.launcher,
            "config_cls": FakeConfig,
            "codex_proposer_factory": self.proposer_factory,
        }
        arguments.update(overrides)
        return omni_pipeline.run_omni("seed", **arguments)

    def _run_pypi_optimization(self, task: dict[str, object]) -> tuple[object, PublicResultLauncher]:
        _launcher, config_cls = omni_pipeline._load_launcher(None, None)
        public_launcher = PublicResultLauncher(config_cls)
        result = omni_pipeline.run_optimization(
            "seed",
            task=task,
            engine="gepa",
            max_evals=4,
            max_token_cost=None,
            run_dir="/external/runs/public-gepa",
            output_dir="/external/outputs/public-gepa",
            launcher=public_launcher,
            codex_proposer_factory=self.proposer_factory,
        )
        return result, public_launcher

    def test_partition_budget_creates_four_equal_slices(self) -> None:
        slices = omni_pipeline.partition_budget(40, 20.0)

        self.assertEqual(
            slices,
            (
                omni_pipeline.BudgetSlice(10, 5.0),
                omni_pipeline.BudgetSlice(10, 5.0),
                omni_pipeline.BudgetSlice(10, 5.0),
                omni_pipeline.BudgetSlice(10, 5.0),
            ),
        )

    def test_pypi_gepa_014_config_uses_public_nested_api(self) -> None:
        _launcher, config_cls = omni_pipeline._load_launcher(None, None)
        self.assertEqual(config_cls.__name__, "GEPAConfig")
        config = omni_pipeline._make_pypi_config(
            config_cls,
            omni_pipeline.BudgetSlice(4, None),
            run_dir=Path("/external/runs/public-gepa"),
            stop_at_score=1.0,
            max_concurrency=2,
            agent_backend="codex",
            agent_model=None,
            codex_model="gpt-5-codex",
            codex_command="codex",
            codex_timeout_seconds=45.0,
            codex_proposer_factory=self.proposer_factory,
            gepa_parallel_proposals=None,
        )
        self.assertEqual(config.engine.max_metric_calls, 4)
        self.assertEqual(config.engine.max_workers, 2)
        self.assertIsNotNone(config.reflection.custom_candidate_proposer)
        self.assertEqual(self.proposer_calls[0]["model"], "gpt-5-codex")

    def test_pypi_token_budget_requires_codex_pricing_and_forwards_cap(self) -> None:
        _launcher, config_cls = omni_pipeline._load_launcher(None, None)
        with self.assertRaisesRegex(ValueError, "pricing rates"):
            omni_pipeline._make_pypi_config(
                config_cls,
                omni_pipeline.BudgetSlice(4, 1.0),
                run_dir=Path("/external/runs/pypi-priced"),
                stop_at_score=None,
                max_concurrency=1,
                agent_backend="codex",
                agent_model=None,
                codex_model=None,
                codex_command="codex",
                codex_timeout_seconds=45.0,
                codex_proposer_factory=self.proposer_factory,
            )

        config = omni_pipeline._make_pypi_config(
            config_cls,
            omni_pipeline.BudgetSlice(4, 1.0),
            run_dir=Path("/external/runs/pypi-priced"),
            stop_at_score=None,
            max_concurrency=1,
            agent_backend="codex",
            agent_model=None,
            codex_model=None,
            codex_command="codex",
            codex_timeout_seconds=45.0,
            codex_input_cost_per_million=2.0,
            codex_output_cost_per_million=8.0,
            codex_proposer_factory=self.proposer_factory,
        )
        self.assertEqual(config.engine.max_metric_calls, 4)
        self.assertEqual(self.proposer_calls[-1]["max_token_cost"], 1.0)
        self.assertEqual(self.proposer_calls[-1]["input_cost_per_million"], 2.0)
        self.assertEqual(self.proposer_calls[-1]["output_cost_per_million"], 8.0)

    def test_pypi_launcher_keeps_test_set_out_of_public_call(self) -> None:
        batch_calls: list[object] = []
        result, public_launcher = self._run_pypi_optimization(
            {
                "evaluator": lambda _candidate, _example: (0.5, {}),
                "batch_evaluator": lambda pairs: batch_calls.append(pairs),
                "dataset": ["train"],
                "valset": ["validation"],
                "test_set": ["held-out"],
            }
        )
        self.assertNotIn("test_set", public_launcher.kwargs)
        self.assertEqual(result.best_candidate, "seed")
        self.assertEqual(result.metadata["test_score"], 0.5)
        self.assertEqual(batch_calls, [])

    def test_explicit_pypi_launcher_does_not_infer_native_omni(self) -> None:
        _launcher, config_cls = omni_pipeline._load_launcher(None, None)
        public_launcher = PublicResultLauncher(config_cls)

        with self.assertRaisesRegex(ValueError, "selected GEPA launcher config") as raised:
            omni_pipeline.run_omni(
                "seed",
                task=self.task,
                max_evals=4,
                max_token_cost=None,
                run_dir="/external/runs/public-gepa",
                output_dir="/external/outputs/public-gepa",
                launcher=public_launcher,
            )

        self.assertNotIn("GEPA_REF", str(raised.exception))
        self.assertEqual(public_launcher.best_of_calls, 0)
        self.assertEqual(public_launcher.optimize_calls, 0)

    def test_pypi_held_out_scoring_supports_batch_evaluator_scalars(self) -> None:
        batch_calls: list[list[tuple[str, object]]] = []

        def batch_evaluator(pairs: list[tuple[str, object]]) -> list[float]:
            batch_calls.append(pairs)
            return [0.25, 0.75]

        result, _public_launcher = self._run_pypi_optimization(
            {
                "batch_evaluator": batch_evaluator,
                "dataset": ["train"],
                "valset": ["validation"],
                "test_set": ["first", "second"],
            }
        )

        self.assertEqual(batch_calls, [[("seed", "first"), ("seed", "second")]])
        self.assertEqual(result.metadata["test_scores"], [0.25, 0.75])
        self.assertEqual(result.metadata["test_score"], 0.5)

    def test_pypi_held_out_scoring_supports_batch_evaluator_tuples(self) -> None:
        def batch_evaluator(_pairs: list[tuple[str, object]]) -> list[tuple[float, dict[str, str]]]:
            return [(0.4, {"detail": "one"}), (0.6, {"detail": "two"})]

        result, _public_launcher = self._run_pypi_optimization(
            {
                "batch_evaluator": batch_evaluator,
                "dataset": ["train"],
                "valset": ["validation"],
                "test_set": ["first", "second"],
            }
        )

        self.assertEqual(result.metadata["test_scores"], [0.4, 0.6])
        self.assertEqual(result.metadata["test_score"], 0.5)

    def test_pypi_held_out_batch_evaluator_requires_one_result_per_example(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one result per test example"):
            self._run_pypi_optimization(
                {
                    "batch_evaluator": lambda _pairs: [0.5],
                    "dataset": ["train"],
                    "valset": ["validation"],
                    "test_set": ["first", "second"],
                }
            )

    def test_omni_runs_three_shared_explorers_then_fresh_gepa(self) -> None:
        result = self._run_omni()

        self.assertEqual(result.best_candidate, "final-candidate")
        self.assertEqual(len(self.launcher.best_of_calls), 1)
        self.assertEqual(len(self.launcher.optimize_calls), 1)
        seed, explore_kwargs = self.launcher.best_of_calls[0]
        self.assertEqual(seed, "seed")
        self.assertIs(explore_kwargs["evaluator"], self.evaluator)
        self.assertNotIn("test_set", explore_kwargs)
        self.assertNotIn("config", explore_kwargs)

        configs = explore_kwargs["configs"]
        self.assertEqual(len(configs), 3)
        self.assertEqual(
            [config.kwargs["engine"] for config in configs],
            ["gepa", "autoresearch", "meta_harness"],
        )
        self.assertEqual(
            [(config.kwargs["max_evals"], config.kwargs["max_token_cost"]) for config in configs],
            [(10, 5.0), (10, 5.0), (10, 5.0)],
        )
        self.assertEqual(
            [config.kwargs["engine_config"]["agent_backend"] for config in configs[1:]],
            ["codex", "codex"],
        )
        self.assertEqual(configs[1].kwargs["engine_config"]["codex_command"], "codex")
        self.assertEqual(configs[1].kwargs["engine_config"]["codex_input_cost_per_million"], 2.0)
        self.assertEqual(configs[1].kwargs["engine_config"]["codex_output_cost_per_million"], 8.0)
        self.assertEqual(configs[2].kwargs["engine_config"]["timeout_seconds"], 600.0)
        self.assertTrue(configs[1].kwargs["engine_config"]["ralph"])
        self.assertEqual(configs[2].kwargs["engine_config"]["max_candidates_per_iter"], 3)
        self.assertEqual(configs[0].kwargs["engine_config"]["engine"], {"max_workers": 1, "parallel": False})

        final_seed, final_kwargs = self.launcher.optimize_calls[0]
        self.assertEqual(final_seed, "phase-1-winner")
        final_config = final_kwargs["config"]
        self.assertEqual(final_config.kwargs["engine"], "gepa")
        self.assertEqual(final_config.kwargs["max_evals"], 10)
        self.assertEqual(final_config.kwargs["max_token_cost"], 5.0)
        self.assertEqual(final_kwargs["test_set"], ["held-out"])
        self.assertIs(final_kwargs["evaluator"], self.evaluator)

        run_dirs = [config.kwargs["run_dir"] for config in configs]
        run_dirs.append(final_config.kwargs["run_dir"])
        self.assertEqual(len(set(run_dirs)), 4)
        self.assertEqual(len(self.proposer_dirs), 2)
        self.assertNotEqual(self.proposer_dirs[0], self.proposer_dirs[1])
        self.assertEqual(result.metadata["omni"]["continuation_engine"], "gepa")

    def test_continuation_engine_can_be_overridden_for_agentic_runs(self) -> None:
        for engine in ("autoresearch", "meta_harness"):
            with self.subTest(engine=engine):
                self.launcher = FakeLauncher()
                self.proposer_dirs = []
                self._run_omni(continuation_engine=engine)
                config = self.launcher.optimize_calls[0][1]["config"]
                self.assertEqual(config.kwargs["engine"], engine)
                self.assertEqual(config.kwargs["engine_config"]["agent_backend"], "codex")
                self.assertEqual(len(self.proposer_dirs), 1)

    def test_agent_backend_and_codex_settings_propagate_to_both_engines(self) -> None:
        self._run_omni(
            agent_backend="codex",
            codex_model="gpt-5-codex",
            codex_command="/custom/bin/codex",
            codex_input_cost_per_million=2.0,
            codex_output_cost_per_million=8.0,
            codex_timeout_seconds=45.0,
        )
        configs = self.launcher.best_of_calls[0][1]["configs"]
        autoresearch_config = configs[1].kwargs["engine_config"]
        meta_config = configs[2].kwargs["engine_config"]
        for config in (autoresearch_config, meta_config):
            self.assertEqual(config["agent_backend"], "codex")
            self.assertEqual(config["codex_command"], "/custom/bin/codex")
            self.assertEqual(config["model"], "gpt-5-codex")
            self.assertEqual(config["codex_input_cost_per_million"], 2.0)
            self.assertEqual(config["codex_output_cost_per_million"], 8.0)
        self.assertEqual(autoresearch_config["max_no_eval_seconds"], 300)
        self.assertEqual(meta_config["timeout_seconds"], 45.0)

    def test_backend_specific_models_propagate_to_gepa_agents_and_continuation(self) -> None:
        cases = (
            (
                "codex",
                {
                    "codex_model": "gpt-5-codex",
                    "pi_model": "ignored/pi-model",
                    "codex_command": "/custom/bin/codex",
                    "codex_proposer_factory": self.proposer_factory,
                },
            ),
            (
                "pi",
                {
                    "codex_model": "ignored-codex-model",
                    "pi_model": "provider/pi-model",
                    "pi_command": "/custom/bin/pi",
                    "pi_proposer_factory": self.proposer_factory,
                },
            ),
        )
        for backend, model_options in cases:
            with self.subTest(backend=backend):
                self.launcher = FakeLauncher()
                self.proposer_dirs = []
                self.proposer_calls = []
                options = {"agent_backend": backend, "agent_model": "legacy/model"}
                options.update(model_options)
                self._run_omni(**options)

                configs = self.launcher.best_of_calls[0][1]["configs"]
                selected_model = model_options[f"{backend}_model"]
                self.assertEqual(
                    [config.kwargs["engine_config"]["model"] for config in configs[1:]],
                    [selected_model, selected_model],
                )
                self.assertEqual([call["model"] for call in self.proposer_calls], [selected_model, selected_model])
                self.assertEqual([call["sandbox"] for call in self.proposer_calls], [True, True])
                if backend == "codex":
                    self.assertEqual(
                        [call["codex_command"] for call in self.proposer_calls],
                        ["/custom/bin/codex", "/custom/bin/codex"],
                    )
                else:
                    self.assertEqual(
                        [call["pi_command"] for call in self.proposer_calls],
                        ["/custom/bin/pi", "/custom/bin/pi"],
                    )

    def test_run_optimization_forwards_backend_specific_selection_to_omni(self) -> None:
        omni_pipeline.run_optimization(
            "seed",
            task=self.task,
            max_evals=40,
            max_token_cost=20.0,
            run_dir="/external/runs/forwarded",
            output_dir="/external/outputs/forwarded",
            launcher=self.launcher,
            config_cls=FakeConfig,
            agent_backend="pi",
            agent_model="legacy/model",
            pi_model="provider/pi-model",
            pi_proposer_factory=self.proposer_factory,
        )

        configs = self.launcher.best_of_calls[0][1]["configs"]
        self.assertEqual(
            [config.kwargs["engine_config"]["model"] for config in configs[1:]],
            ["provider/pi-model", "provider/pi-model"],
        )
        self.assertEqual(
            [call["model"] for call in self.proposer_calls],
            ["provider/pi-model", "provider/pi-model"],
        )

    def test_existing_narrow_codex_factory_remains_supported(self) -> None:
        calls: list[tuple[Path, str | None, float]] = []

        def narrow_factory(run_dir: Path, model: str | None, timeout_seconds: float) -> object:
            calls.append((run_dir, model, timeout_seconds))
            return object()

        self._run_omni(
            codex_model="gpt-5-codex",
            codex_proposer_factory=narrow_factory,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual([call[1] for call in calls], ["gpt-5-codex", "gpt-5-codex"])

    def test_legacy_agent_model_falls_back_for_codex_and_pi(self) -> None:
        for backend, factory_name in (("codex", "codex_proposer_factory"), ("pi", "pi_proposer_factory")):
            with self.subTest(backend=backend):
                self.launcher = FakeLauncher()
                self.proposer_dirs = []
                self.proposer_calls = []
                self._run_omni(
                    agent_backend=backend,
                    agent_model="legacy/model",
                    **{factory_name: self.proposer_factory},
                )

                configs = self.launcher.best_of_calls[0][1]["configs"]
                self.assertEqual(
                    [config.kwargs["engine_config"]["model"] for config in configs[1:]],
                    ["legacy/model", "legacy/model"],
                )
                self.assertEqual([call["model"] for call in self.proposer_calls], ["legacy/model", "legacy/model"])

    def test_gepa_parallel_proposals_are_opt_in_and_use_pxn_strategies(self) -> None:
        class FakePxNSampling:
            def __init__(self, *, p: int, n: int) -> None:
                self.p = p
                self.n = n

        class FakeAllImprovements:
            pass

        gepa_package = ModuleType("gepa")
        gepa_package.__path__ = []  # type: ignore[attr-defined]
        strategies_package = ModuleType("gepa.strategies")
        strategies_package.__path__ = []  # type: ignore[attr-defined]
        sampling_module = ModuleType("gepa.strategies.proposal_sampling")
        sampling_module.PxNSampling = FakePxNSampling  # type: ignore[attr-defined]
        selection_module = ModuleType("gepa.strategies.proposal_selection")
        selection_module.AllImprovements = FakeAllImprovements  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "gepa": gepa_package,
                "gepa.strategies": strategies_package,
                "gepa.strategies.proposal_sampling": sampling_module,
                "gepa.strategies.proposal_selection": selection_module,
            },
        ):
            self._run_omni(
                max_concurrency=4,
                gepa_parallel_proposals=(2, 3),
            )

        configs = self.launcher.best_of_calls[0][1]["configs"]
        gepa_configs = [config for config in configs if config.kwargs["engine"] == "gepa"]
        gepa_configs.append(self.launcher.optimize_calls[0][1]["config"])
        for config in gepa_configs:
            engine = config.kwargs["engine_config"]["engine"]
            self.assertEqual(engine["max_workers"], 4)
            self.assertTrue(engine["parallel"])
            self.assertIsInstance(engine["sampling_strategy"], FakePxNSampling)
            self.assertEqual((engine["sampling_strategy"].p, engine["sampling_strategy"].n), (2, 3))
            self.assertIsInstance(engine["selection_strategy"], FakeAllImprovements)

    def test_gepa_parallel_proposals_reject_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integers"):
            self._run_omni(gepa_parallel_proposals=(0, 2))
        with self.assertRaisesRegex(ValueError, "tuple"):
            self._run_omni(gepa_parallel_proposals=[2, 2])

    def test_codex_token_budget_does_not_require_pricing_rates(self) -> None:
        result = self._run_omni(
            max_token_cost=20.0,
            codex_input_cost_per_million=None,
            codex_output_cost_per_million=None,
        )

        self.assertEqual(result.best_candidate, "final-candidate")

    def test_pi_and_claude_remain_explicit_agent_backend_options(self) -> None:
        for backend in ("pi", "claude"):
            with self.subTest(backend=backend):
                self.launcher = FakeLauncher()
                self.proposer_dirs = []
                self.proposer_calls = []
                self._run_omni(agent_backend=backend, agent_model="provider/model")
                configs = self.launcher.best_of_calls[0][1]["configs"]
                self.assertEqual(
                    [config.kwargs["engine_config"]["agent_backend"] for config in configs[1:]],
                    [backend, backend],
                )
                if backend == "pi":
                    self.assertEqual(configs[1].kwargs["engine_config"]["pi_command"], "pi")
                else:
                    self.assertNotIn("pi_command", configs[1].kwargs["engine_config"])
                    self.assertNotIn("codex_command", configs[1].kwargs["engine_config"])

    def test_claude_does_not_invent_a_provider_specific_model(self) -> None:
        self._run_omni(agent_backend="claude")

        configs = self.launcher.best_of_calls[0][1]["configs"]
        self.assertTrue(all("model" not in config.kwargs["engine_config"] for config in configs[1:]))

    def test_default_run_optimization_uses_omni_and_standalone_bypasses_it(self) -> None:
        result = omni_pipeline.run_optimization(
            "seed",
            task=self.task,
            max_evals=40,
            max_token_cost=20.0,
            codex_input_cost_per_million=2.0,
            codex_output_cost_per_million=8.0,
            run_dir="/external/runs/default",
            output_dir="/external/outputs/default",
            launcher=self.launcher,
            config_cls=FakeConfig,
            codex_proposer_factory=self.proposer_factory,
        )
        self.assertEqual(result.best_candidate, "final-candidate")
        self.assertEqual(len(self.launcher.best_of_calls), 1)
        self.assertEqual(len(self.launcher.optimize_calls), 1)

        self.launcher = FakeLauncher()
        standalone = omni_pipeline.run_optimization(
            "seed",
            task=self.task,
            engine="best_of_n",
            max_evals=12,
            max_token_cost=3.0,
            codex_input_cost_per_million=2.0,
            codex_output_cost_per_million=8.0,
            run_dir="/external/runs/standalone",
            output_dir="/external/outputs/standalone",
            launcher=self.launcher,
            config_cls=FakeConfig,
            codex_proposer_factory=self.proposer_factory,
        )
        self.assertEqual(standalone.best_candidate, "final-candidate")
        self.assertEqual(self.launcher.best_of_calls, [])
        self.assertEqual(len(self.launcher.optimize_calls), 1)
        _, standalone_kwargs = self.launcher.optimize_calls[0]
        self.assertEqual(standalone_kwargs["test_set"], ["held-out"])
        self.assertEqual(standalone_kwargs["config"].kwargs["max_evals"], 12)
        self.assertEqual(standalone_kwargs["config"].kwargs["max_token_cost"], 3.0)

    def test_omni_requires_four_positive_evaluation_slices(self) -> None:
        with self.assertRaisesRegex(omni_pipeline.OmniBudgetError, "at least four"):
            self._run_omni(max_evals=3, max_token_cost=None)

        with self.assertRaisesRegex(omni_pipeline.OmniBudgetError, "explicit"):
            self._run_omni(max_evals=None, max_token_cost=None)

    def test_unsupported_engine_is_not_a_router(self) -> None:
        with self.assertRaisesRegex(ValueError, "omit engine for Omni"):
            omni_pipeline.run_optimization(
                "seed",
                task=self.task,
                engine="omni",
                max_evals=40,
                max_token_cost=None,
                run_dir="/external/runs/invalid",
                output_dir="/external/outputs/invalid",
                launcher=self.launcher,
                config_cls=FakeConfig,
            )

    def test_unsandboxed_and_in_checkout_runs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sandbox must be True"):
            self._run_omni(sandbox=False)
        with self.assertRaisesRegex(ValueError, "outside the development checkout"):
            self._run_omni(run_dir=Path(__file__).resolve().parents[1] / "runs")

        with self.assertRaisesRegex(ValueError, "outside the development checkout"):
            omni_pipeline.run_optimization(
                "seed",
                task=self.task,
                engine="best_of_n",
                max_evals=12,
                max_token_cost=None,
                run_dir="/external/runs/standalone",
                output_dir=Path(__file__).resolve().parents[1] / "outputs",
                launcher=self.launcher,
                config_cls=FakeConfig,
            )

    def test_native_best_of_n_is_independent_and_scores_heldout_afterwards(self) -> None:
        calls: list[dict[str, object]] = []

        class Runner:
            def __init__(self, **kwargs: object) -> None:
                calls.append(dict(kwargs))

            def run(self, _prompt: str, **kwargs: object) -> AgentRunResult:
                work_dir = Path(kwargs["work_dir"])
                return AgentRunResult(
                    final_text=f"```\n{work_dir.name}\n```",
                    session_id=None,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=0.0,
                    returncode=0,
                    command=("fake",),
                    stdout="",
                    stderr="",
                )

            def close(self) -> None:
                return

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = omni_pipeline.run_optimization(
                "seed",
                task={
                    "evaluator": lambda candidate, example: (1.0 if "sample-0002" in candidate else 0.5, {}),
                    "dataset": ["train"],
                    "test_set": ["held-out"],
                },
                engine="best_of_n",
                max_evals=2,
                max_token_cost=None,
                run_dir=root / "runs",
                output_dir=root / "outputs",
                native_runner_factory=lambda **kwargs: Runner(**kwargs),
            )

        self.assertIn("sample-0002", result.best_candidate)
        self.assertEqual(result.metadata["heldout_scores"], [1.0])
        self.assertEqual(result.metadata["test_scores"], [1.0])
        self.assertEqual(result.metadata["test_score"], 1.0)
        self.assertEqual([call["command"] for call in calls], ["codex", "codex"])
        self.assertNotEqual(result.metadata["work_dir"], str(calls[0]["work_dir"]))

    def test_native_omni_runs_three_branches_then_fresh_continuation(self) -> None:
        calls: list[tuple[str, str | None, Path, str]] = []

        class Runner:
            def __init__(self, **kwargs: object) -> None:
                self.backend = str(kwargs["backend"])

            def run(self, prompt: str, **kwargs: object) -> AgentRunResult:
                work_dir = Path(kwargs["work_dir"])
                label = "continuation" if "phase-2" in str(work_dir) else work_dir.name
                calls.append((label, kwargs.get("session_id"), work_dir, prompt))
                if label == "meta_harness":
                    agents = work_dir / "agents"
                    agents.mkdir(parents=True, exist_ok=True)
                    (agents / "candidate.txt").write_text("meta-winner", encoding="utf-8")
                    pending = work_dir / "state" / "pending_eval_iter1.json"
                    pending.parent.mkdir(parents=True, exist_ok=True)
                    pending.write_text(
                        '{"candidates":[{"name":"candidate","file":"agents/candidate.txt"}]}\n',
                        encoding="utf-8",
                    )
                return AgentRunResult(
                    final_text="```\ncontinuation\n```",
                    session_id="session-1",
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=0.0,
                    returncode=0,
                    command=("fake",),
                    stdout="",
                    stderr="",
                )

            def close(self) -> None:
                return

        gepa_result = SimpleNamespace(best_candidate="gepa-winner", best_score=0.2, metadata={})
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(omni_pipeline, "run_optimization", return_value=gepa_result) as gepa_run,
        ):
            root = Path(temp)
            result = omni_pipeline.run_omni(
                "seed",
                task={
                    "evaluator": lambda candidate, _example: (1.0 if candidate == "meta-winner" else 0.1, {}),
                    "dataset": [1],
                    "test_set": ["held-out"],
                },
                max_evals=8,
                max_token_cost=None,
                run_dir=root / "runs",
                output_dir=root / "outputs",
                continuation_engine="best_of_n",
                native_runner_factory=lambda **kwargs: Runner(**kwargs),
                max_n=1,
                max_iterations=1,
            )

        self.assertEqual(len(result.metadata["omni"]["branches"]), 3)
        self.assertEqual(
            [branch["engine"] for branch in result.metadata["omni"]["branches"]],
            ["gepa", "autoresearch", "meta_harness"],
        )
        self.assertEqual(result.metadata["omni"]["continuation_engine"], "best_of_n")
        self.assertEqual(gepa_run.call_count, 1)
        self.assertNotIn("test_set", gepa_run.call_args.kwargs["task"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1][0], "continuation")
        autoresearch_prompt = next(prompt for label, _session, _work_dir, prompt in calls if label == "autoresearch")
        self.assertIn("seed", autoresearch_prompt)
        self.assertIn("Improve the candidate", autoresearch_prompt)
        meta_prompt = next(prompt for label, _session, _work_dir, prompt in calls if label == "meta_harness")
        self.assertIn("Meta-Harness task", meta_prompt)
        self.assertIn("seed", meta_prompt)
        self.assertNotIn("test_set", json.dumps(result.metadata["omni"]))


if __name__ == "__main__":
    unittest.main()
