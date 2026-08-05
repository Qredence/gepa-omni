from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gepa-omni-skill"
    / "scripts"
)
_SCRIPT_DIR_STR = str(SCRIPT_DIR)
sys.path.insert(0, _SCRIPT_DIR_STR)
try:
    import omni_pipeline  # noqa: E402
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


class OmniPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = FakeLauncher()
        self.proposer_dirs: list[Path] = []

        def proposer_factory(**kwargs: object) -> object:
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
            "launcher": self.launcher,
            "config_cls": FakeConfig,
            "codex_proposer_factory": self.proposer_factory,
        }
        arguments.update(overrides)
        return omni_pipeline.run_omni("seed", **arguments)

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
            ["pi", "pi"],
        )
        self.assertTrue(configs[1].kwargs["engine_config"]["ralph"])
        self.assertEqual(configs[2].kwargs["engine_config"]["max_candidates_per_iter"], 3)

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
                self.assertEqual(config.kwargs["engine_config"]["agent_backend"], "pi")
                self.assertEqual(len(self.proposer_dirs), 1)

    def test_default_run_optimization_uses_omni_and_standalone_bypasses_it(self) -> None:
        result = omni_pipeline.run_optimization(
            "seed",
            task=self.task,
            max_evals=40,
            max_token_cost=20.0,
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


if __name__ == "__main__":
    unittest.main()
