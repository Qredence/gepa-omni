from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))
PROPOSER_DIR = (
    PROJECT_ROOT
    / "skills"
    / "gepa-omni-skill"
    / "scripts"
)
sys.path.insert(0, str(PROPOSER_DIR))

import gepa_self_evaluate as self_evaluate_module  # noqa: E402
import codex_agent_proposer as proposer_module  # noqa: E402
from codex_agent_proposer import CodexAgentProposer, CodexProcessError  # noqa: E402


class SelfEvaluateTests(unittest.TestCase):
    def test_prompt_and_extracted_helpers_keep_contract(self) -> None:
        prompt = CodexAgentProposer._prompt(["prompt", "rubric"])
        for required in (
            "candidate.json",
            "reflective_dataset.json",
            "new_texts",
            "read-only sandbox",
            "Do not return Markdown fences",
        ):
            self.assertIn(required, prompt)
        self.assertIn(
            'The requested component names are exactly ["prompt", "rubric"]',
            prompt,
        )
        self.assertNotIn("\\n", prompt)

        with tempfile.TemporaryDirectory() as temp_dir:
            proposal_dir = Path(temp_dir)
            output_path = proposal_dir / "codex_result.json"
            output_path.write_text(
                json.dumps({"new_texts": {"prompt": "new"}}), encoding="utf-8"
            )
            proposer = CodexAgentProposer(proposal_dir)
            self.assertEqual(
                proposer._parse_candidate_response(
                    output_path, "", ["prompt"], proposal_dir
                ),
                {"prompt": "new"},
            )
            with patch.object(
                proposer_module.subprocess,
                "Popen",
                side_effect=OSError("unavailable"),
            ):
                with self.assertRaises(CodexProcessError):
                    proposer._start_process([], proposal_dir)

    def _result(
        self, command: list[str], returncode: int = 0
    ) -> self_evaluate_module.CommandResult:
        return self_evaluate_module.CommandResult(tuple(command), returncode, "", "")

    def test_passing_candidate_uses_plugin_score(self) -> None:
        report = json.dumps(
            {
                "summary": {"score": 93},
                "checks": [{"id": "remaining-warning", "status": "warn"}],
            }
        )

        def run(
            command: list[str], cwd: Path, timeout_seconds: float
        ) -> self_evaluate_module.CommandResult:
            if "plugin-eval" in " ".join(command) or "node" in command:
                return self_evaluate_module.CommandResult(tuple(command), 0, report, "")
            return self._result(command)

        with patch.object(self_evaluate_module, "_run_command", side_effect=run):
            result = self_evaluate_module.evaluate_candidate(
                "candidate = True\n",
                plugin_eval_command=["node", "plugin-eval.js"],
            )

        self.assertTrue(result.valid)
        self.assertEqual(result.score, 0.93)
        self.assertEqual(result.info["plugin_eval_warning_ids"], ["remaining-warning"])

    def test_failed_hard_gate_scores_zero(self) -> None:
        report = json.dumps({"summary": {"score": 99}, "checks": []})

        def run(
            command: list[str], cwd: Path, timeout_seconds: float
        ) -> self_evaluate_module.CommandResult:
            if "pytest" in command:
                return self._result(command, returncode=1)
            if "plugin-eval" in " ".join(command) or "node" in command:
                return self_evaluate_module.CommandResult(tuple(command), 0, report, "")
            return self._result(command)

        with patch.object(self_evaluate_module, "_run_command", side_effect=run):
            result = self_evaluate_module.evaluate_candidate(
                "candidate = False\n",
                plugin_eval_command=["node", "plugin-eval.js"],
            )

        self.assertFalse(result.valid)
        self.assertEqual(result.score, 0.0)
        self.assertFalse(result.info["tests_passed"])

    def test_malformed_plugin_score_scores_zero(self) -> None:
        report = json.dumps({"summary": {"score": "not-a-number"}, "checks": []})

        def run(
            command: list[str], cwd: Path, timeout_seconds: float
        ) -> self_evaluate_module.CommandResult:
            if "plugin-eval" in " ".join(command) or "node" in command:
                return self_evaluate_module.CommandResult(tuple(command), 0, report, "")
            return self._result(command)

        with patch.object(self_evaluate_module, "_run_command", side_effect=run):
            result = self_evaluate_module.evaluate_candidate(
                "candidate = False\n",
                plugin_eval_command=["node", "plugin-eval.js"],
            )

        self.assertFalse(result.valid)
        self.assertEqual(result.score, 0.0)
        self.assertIn("summary.score", result.info["analysis_error"])

    def test_all_failed_proposals_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            error_dir = run_dir / "proposer" / "proposals" / "proposal-1"
            error_dir.mkdir(parents=True)
            (error_dir / "error.txt").write_text("CodexProposalTimeout\n")

            self.assertTrue(
                self_evaluate_module._all_proposals_failed(
                    run_dir, seed="same", best="same"
                )
            )
            self.assertFalse(
                self_evaluate_module._all_proposals_failed(
                    run_dir, seed="seed", best="changed"
                )
            )
            successful_dir = run_dir / "proposer" / "proposals" / "proposal-success"
            successful_dir.mkdir(parents=True)
            self.assertFalse(
                self_evaluate_module._all_proposals_failed(
                    run_dir, seed="same", best="same"
                )
            )

    def test_main_fails_when_all_live_proposals_time_out(self) -> None:
        class TimeoutProposer:
            def __init__(self, run_dir: Path, **_kwargs: object) -> None:
                error_dir = run_dir / "proposals" / "proposal-timeout"
                error_dir.mkdir(parents=True)
                (error_dir / "error.txt").write_text(
                    "CodexProposalTimeout: exceeded 600s\n", encoding="utf-8"
                )

        seed = (
            self_evaluate_module.PLUGIN_ROOT / self_evaluate_module.TARGET_RELATIVE
        ).read_text(encoding="utf-8")
        result = SimpleNamespace(
            best_candidate={self_evaluate_module.COMPONENT: seed},
            best_score=0.91,
            total_evals=1,
            metadata={"engine": "gepa", "output_dir": "output"},
        )
        baseline = self_evaluate_module.CandidateEvaluation(
            0.91,
            True,
            {
                "plugin_eval_score": 91.0,
                "plugin_eval_warning_ids": [],
            },
        )

        class FakeConfig:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        captured: dict[str, object] = {}

        def fake_optimize(**kwargs: object) -> object:
            captured.update(kwargs)
            return result

        gepa_package = ModuleType("gepa")
        gepa_optimize = ModuleType("gepa.optimize_anything")
        gepa_optimize.EngineConfig = FakeConfig
        gepa_optimize.OptimizeAnythingConfig = FakeConfig
        gepa_optimize.ReflectionConfig = FakeConfig
        gepa_optimize.optimize_anything = fake_optimize
        gepa_package.optimize_anything = gepa_optimize
        with tempfile.TemporaryDirectory() as temp_dir:
            stderr = io.StringIO()
            with (
                patch.object(
                    self_evaluate_module,
                    "_plugin_eval_command",
                    return_value=["plugin-eval"],
                ),
                patch.object(
                    self_evaluate_module,
                    "_run_command",
                    return_value=self._result(["preflight"]),
                ),
                patch.object(
                    self_evaluate_module, "evaluate_candidate", return_value=baseline
                ),
                patch.object(proposer_module, "CodexAgentProposer", TimeoutProposer),
                patch.dict(
                    sys.modules,
                    {
                        "gepa": gepa_package,
                        "gepa.optimize_anything": gepa_optimize,
                    },
                ),
                redirect_stderr(stderr),
            ):
                exit_code = self_evaluate_module.main(
                    [
                        "--model",
                        "test-model",
                        "--run-dir",
                        temp_dir,
                        "--plugin-eval-command",
                        "plugin-eval",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("All proposal attempts failed", stderr.getvalue())
        config = captured["config"]
        self.assertEqual(config.kwargs["engine"], "gepa")
        self.assertEqual(config.kwargs["max_evals"], 6)
        self.assertIn("reflection", config.kwargs["engine_config"])
        self.assertEqual(captured["test_set"], [{"phase": "held-out"}])

    def test_run_directory_must_be_external(self) -> None:
        with self.assertRaises(ValueError):
            self_evaluate_module._validate_run_dir(
                self_evaluate_module.REPO_ROOT / "runs"
            )


if __name__ == "__main__":
    unittest.main()
