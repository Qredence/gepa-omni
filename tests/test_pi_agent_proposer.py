from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# fmt: off
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import pi_agent_proposer as proposer_module  # noqa: E402
from pi_agent_proposer import (  # noqa: E402
    PiAgentProposer,
    PiProcessError,
    PiProposalTimeout,
    PiProposalValidationError,
)


class FakePiRunner:
    calls: list[tuple[dict[str, object], str]] = []
    response: object = {"new_texts": {"prompt": "improved"}, "summary": "better"}
    returncode = 0
    timed_out = False
    completed = True

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.command = [
            "pi",
            "--mode",
            "json",
            "--no-session",
            "--no-context-files",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-approve",
            "--tools",
            str(kwargs["tools"]),
        ]

    def run(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((kwargs, prompt))
        raw = self.response if isinstance(self.response, str) else json.dumps(self.response)
        stdout = json.dumps({"type": "message_end", "message": {"content": raw}}) + "\n"
        return SimpleNamespace(
            command=tuple(self.command),
            returncode=self.returncode,
            stdout=stdout,
            stderr="pi stderr" if self.returncode else "",
            session_id="pi-session",
            usage={"input_tokens": 4, "output_tokens": 6},
            cost_usd=0.02,
            timed_out=self.timed_out,
            completed=self.completed,
            final_text=raw,
        )

    def close(self) -> None:
        return None


class PiAgentProposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tempdir.name)
        FakePiRunner.calls = []
        FakePiRunner.response = {"new_texts": {"prompt": "improved"}, "summary": "better"}
        FakePiRunner.returncode = 0
        FakePiRunner.timed_out = False
        FakePiRunner.completed = True

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _proposer(self) -> PiAgentProposer:
        return PiAgentProposer(self.run_dir, model="provider/model", sandbox=True, timeout_seconds=2)

    def test_unsandboxed_or_in_checkout_runs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sandbox must be True"):
            PiAgentProposer(self.run_dir, sandbox=False)
        with self.assertRaisesRegex(ValueError, "outside the development checkout"):
            PiAgentProposer(Path(__file__).resolve().parents[1] / "runs")

    def test_valid_response_and_prompt_contract(self) -> None:
        proposer = self._proposer()
        with patch.object(proposer_module, "_PiAgentRunner", FakePiRunner), patch.object(
            proposer_module, "_pi_sandbox_prefix", lambda path: ["sandbox-exec", "-p", str(path)]
        ):
            result = proposer(
                {"prompt": "seed"},
                {"example": [{"score": 0.2, "feedback": "too vague"}]},
                ["prompt"],
            )
        self.assertEqual(result, {"prompt": "improved"})
        proposal_dir = proposer.last_proposal_dir
        assert proposal_dir is not None
        proposal_dirs = list((self.run_dir / "proposals").iterdir())
        self.assertEqual(len(proposal_dirs), 1)
        self.assertEqual(proposal_dir.resolve(), proposal_dirs[0].resolve())
        prompt = (proposal_dir / "prompt.txt").read_text()
        self.assertIn("candidate.json", prompt)
        self.assertIn('["prompt"]', prompt)
        command = json.loads((proposal_dir / "command.json").read_text())
        self.assertIn("--no-session", command)
        self.assertIn("--no-context-files", command)
        self.assertIn("--no-extensions", command)
        self.assertIn("--no-skills", command)
        self.assertEqual(command[command.index("--tools") + 1], "read,grep,find,ls")
        self.assertTrue((proposal_dir / "usage.json").exists())

    def test_malformed_response_keeps_diagnostics(self) -> None:
        FakePiRunner.response = "not json"
        with patch.object(proposer_module, "_PiAgentRunner", FakePiRunner), patch.object(
            proposer_module, "_pi_sandbox_prefix", lambda path: []
        ):
            with self.assertRaises(PiProposalValidationError) as caught:
                self._proposer()({"prompt": "seed"}, {}, ["prompt"])
        assert caught.exception.proposal_dir is not None
        self.assertIn("structured", (caught.exception.proposal_dir / "error.txt").read_text())
        self.assertTrue((caught.exception.proposal_dir / "pi_stdout.jsonl").exists())

    def test_nonzero_exit_preserves_diagnostics(self) -> None:
        FakePiRunner.returncode = 7
        with patch.object(proposer_module, "_PiAgentRunner", FakePiRunner), patch.object(
            proposer_module, "_pi_sandbox_prefix", lambda path: []
        ):
            with self.assertRaises(PiProcessError) as caught:
                self._proposer()({"prompt": "seed"}, {}, ["prompt"])
        assert caught.exception.proposal_dir is not None
        self.assertIn("status 7", str(caught.exception))
        self.assertEqual((caught.exception.proposal_dir / "pi_stderr.log").read_text(), "pi stderr")

    def test_timeout_is_distinct_from_malformed_output(self) -> None:
        FakePiRunner.timed_out = True
        FakePiRunner.completed = False
        with patch.object(proposer_module, "_PiAgentRunner", FakePiRunner), patch.object(
            proposer_module, "_pi_sandbox_prefix", lambda path: []
        ):
            with self.assertRaises(PiProposalTimeout):
                self._proposer()({"prompt": "seed"}, {}, ["prompt"])

    def test_concurrent_proposals_are_isolated(self) -> None:
        with patch.object(proposer_module, "_PiAgentRunner", FakePiRunner), patch.object(
            proposer_module, "_pi_sandbox_prefix", lambda path: []
        ):
            from concurrent.futures import ThreadPoolExecutor

            proposer = self._proposer()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: proposer({"prompt": "seed"}, {}, ["prompt"]), range(2)))
        self.assertEqual(results, [{"prompt": "improved"}, {"prompt": "improved"}])
        proposal_dirs = sorted((self.run_dir / "proposals").iterdir())
        self.assertEqual(len(proposal_dirs), 2)
        self.assertNotEqual(proposal_dirs[0], proposal_dirs[1])


if __name__ == "__main__":
    unittest.main()
