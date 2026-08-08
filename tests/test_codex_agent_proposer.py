from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

# fmt: off
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import codex_agent_proposer as proposer_module  # noqa: E402
from codex_agent_proposer import (  # noqa: E402
    CodexAgentProposer,
    CodexProcessError,
    CodexProposalTimeout,
    ProposalValidationError,
)


class FakeProcess:
    _pid = 41000
    _pid_lock = threading.Lock()

    def __init__(self, command: list[str], response: object = None, *, returncode: int = 0, timeout: bool = False):
        with self._pid_lock:
            type(self)._pid += 1
            self.pid = type(self)._pid
        self.command = command
        self.response = response
        self.returncode = returncode
        self.timeout = timeout
        self.terminated = False
        self.communicate_calls = 0

    def communicate(self, _input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self.timeout and not self.terminated:
            raise TimeoutExpired(self.command, timeout or 0, output="partial", stderr="partial stderr")
        if self.response is not None:
            output_path = Path(self.command[self.command.index("--output-last-message") + 1])
            raw_response = self.response.raw if isinstance(self.response, RawResponse) else json.dumps(self.response)
            output_path.write_text(raw_response, encoding="utf-8")
        return '{"type":"turn.completed"}\n', ""

    def poll(self) -> int | None:
        return None if not self.terminated and self.timeout else self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.terminated = True
        return self.returncode


class RawResponse:
    def __init__(self, raw: str) -> None:
        self.raw = raw


class CodexAgentProposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _call(
        self,
        response: object,
        *,
        returncode: int = 0,
        timeout: bool = False,
        model: str | None = None,
        codex_command: str = "codex",
        timeout_seconds: float = 1,
    ) -> tuple[dict[str, str] | None, FakeProcess, CodexAgentProposer]:
        holder: dict[str, FakeProcess] = {}

        def make_process(command: list[str], **_kwargs: object) -> FakeProcess:
            process = FakeProcess(command, response, returncode=returncode, timeout=timeout)
            holder["process"] = process
            return process

        proposer = CodexAgentProposer(
            self.run_dir,
            model=model,
            timeout_seconds=timeout_seconds,
            codex_command=codex_command,
        )
        with patch.object(proposer_module.shutil, "which", return_value="/usr/local/bin/codex"), patch.object(
            proposer_module.subprocess, "Popen", side_effect=make_process
        ):
            result = proposer(
                {"prompt": "seed", "rubric": "be precise"},
                {"example": [{"score": 0.2, "feedback": "too vague"}]},
                ["prompt"],
                metadata={"iteration_id": "iter-1"},
            )
        return result, holder["process"], proposer

    def test_in_checkout_run_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the development checkout"):
            CodexAgentProposer(Path(__file__).resolve().parents[1] / "runs")
        with self.assertRaisesRegex(ValueError, "sandbox must be True"):
            CodexAgentProposer(self.run_dir, sandbox=False)

    def test_valid_proposal_materializes_context_and_usage(self) -> None:
        result, process, proposer = self._call(
            {"new_texts": {"prompt": "be precise and concise"}, "summary": "tightened"}
        )
        self.assertEqual(result, {"prompt": "be precise and concise"})
        self.assertEqual(process.returncode, 0)
        proposal_dir = proposer.last_proposal_dir
        assert proposal_dir is not None
        self.assertEqual(json.loads((proposal_dir / "candidate.json").read_text())["prompt"], "seed")
        self.assertEqual(json.loads((proposal_dir / "components_to_update.json").read_text()), ["prompt"])
        self.assertIn("--sandbox", process.command)
        self.assertEqual(process.command[process.command.index("--sandbox") + 1], "read-only")
        usage = json.loads((proposal_dir / "usage.json").read_text())
        self.assertIsNone(usage["usd_cost"])

    def test_missing_component_is_rejected_and_diagnostics_remain(self) -> None:
        with self.assertRaises(ProposalValidationError) as caught:
            self._call({"new_texts": {}})
        proposal_dir = caught.exception.proposal_dir
        assert proposal_dir is not None
        self.assertTrue((proposal_dir / "codex_result.json").exists())
        self.assertIn("missing", (proposal_dir / "error.txt").read_text())

    def test_malformed_json_output_is_rejected(self) -> None:
        with self.assertRaises(ProposalValidationError) as caught:
            self._call(RawResponse("{not valid json"))
        self.assertIn("structured", str(caught.exception))

    def test_extra_component_is_rejected(self) -> None:
        with self.assertRaises(ProposalValidationError) as caught:
            self._call({"new_texts": {"prompt": "new", "extra": "bad"}})
        self.assertIn("extra", str(caught.exception))

    def test_non_string_component_is_rejected(self) -> None:
        with self.assertRaises(ProposalValidationError) as caught:
            self._call({"new_texts": {"prompt": 42}})
        self.assertIn("strings", str(caught.exception))

    def test_nonzero_exit_preserves_stderr(self) -> None:
        holder: dict[str, FakeProcess] = {}

        def make_process(command: list[str], **_kwargs: object) -> FakeProcess:
            process = FakeProcess(command, None, returncode=7)
            holder["process"] = process
            return process

        proposer = CodexAgentProposer(self.run_dir, timeout_seconds=1)
        with patch.object(proposer_module.shutil, "which", return_value="/usr/local/bin/codex"), patch.object(
            proposer_module.subprocess, "Popen", side_effect=make_process
        ), patch.object(FakeProcess, "communicate", return_value=("", "model failed")):
            with self.assertRaises(CodexProcessError) as caught:
                proposer({"prompt": "seed"}, {}, ["prompt"])
        proposal_dir = caught.exception.proposal_dir
        assert proposal_dir is not None
        self.assertEqual((proposal_dir / "codex_stderr.log").read_text(), "model failed")
        self.assertIn("status 7", str(caught.exception))

    def test_timeout_terminates_process_group_and_keeps_partial_output(self) -> None:
        proposer = CodexAgentProposer(self.run_dir, timeout_seconds=0.01)
        with patch.object(proposer_module.shutil, "which", return_value="/usr/local/bin/codex"), patch.object(
            proposer_module.subprocess,
            "Popen",
            side_effect=lambda command, **_kwargs: FakeProcess(command, timeout=True),
        ), patch.object(proposer_module.os, "getpgid", return_value=12345), patch.object(
            proposer_module.os, "killpg"
        ) as killpg:
            with self.assertRaises(CodexProposalTimeout) as caught:
                proposer({"prompt": "seed"}, {}, ["prompt"])
        killpg.assert_called()
        proposal_dir = caught.exception.proposal_dir
        assert proposal_dir is not None
        self.assertIn("partial", (proposal_dir / "codex_stdout.jsonl").read_text())
        self.assertIn("timeout", (proposal_dir / "error.txt").read_text().lower())

    def test_command_uses_only_safe_cli_flags(self) -> None:
        result, process, _proposer = self._call(
            {"new_texts": {"prompt": "new"}},
            model="gpt-5",
            codex_command="/custom/bin/codex",
            timeout_seconds=4,
        )
        self.assertEqual(result, {"prompt": "new"})
        self.assertEqual(process.command[0], "/usr/local/bin/codex")
        self.assertIn("--ephemeral", process.command)
        self.assertIn("--ignore-user-config", process.command)
        self.assertIn("--skip-git-repo-check", process.command)
        self.assertIn("--output-schema", process.command)
        self.assertIn("--output-last-message", process.command)
        self.assertIn("--model", process.command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", process.command)
        self.assertNotIn("danger-full-access", process.command)
        self.assertEqual(process.command[process.command.index("--sandbox") + 1], "read-only")

    def test_concurrent_calls_get_distinct_proposal_directories(self) -> None:
        processes: list[FakeProcess] = []

        def make_process(command: list[str], **_kwargs: object) -> FakeProcess:
            process = FakeProcess(command, {"new_texts": {"prompt": "next"}})
            processes.append(process)
            return process

        proposer = CodexAgentProposer(self.run_dir, timeout_seconds=1)
        with patch.object(proposer_module.shutil, "which", return_value="/usr/local/bin/codex"), patch.object(
            proposer_module.subprocess, "Popen", side_effect=make_process
        ):
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _i: proposer({"prompt": "seed"}, {}, ["prompt"]), range(2)))
        self.assertEqual(results, [{"prompt": "next"}, {"prompt": "next"}])
        proposal_dirs = sorted((self.run_dir / "proposals").iterdir())
        self.assertEqual(len(proposal_dirs), 2)
        self.assertNotEqual(proposal_dirs[0], proposal_dirs[1])
        self.assertEqual(len(processes), 2)


if __name__ == "__main__":
    unittest.main()
