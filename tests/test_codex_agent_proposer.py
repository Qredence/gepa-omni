from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

# fmt: off
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
from codex_agent_proposer import (  # noqa: E402
    CodexAgentProposer,
    CodexProcessError,
    CodexProposalTimeout,
    CodexTokenBudgetExceeded,
    ProposalValidationError,
)
# fmt: on


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ChatProposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tempdir.name)
        self.calls: list[tuple[object, float]] = []
        self.payload: object = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"new_texts": {"prompt": "improved"}, "summary": "better"}),
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        }
        self._saved_env = {name: os.environ.get(name) for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")}
        for name in self._saved_env:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def _opener(self, request: object, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        return FakeResponse(self.payload)

    def _proposer(self, **kwargs: object) -> CodexAgentProposer:
        options: dict[str, object] = {
            "model": "explicit-model",
            "base_url": "https://llm.example/v1",
            "api_key": "explicit-key",
            "opener": self._opener,
            "timeout_seconds": 2,
            "sandbox": True,
        }
        options.update(kwargs)
        return CodexAgentProposer(self.run_dir, **options)

    def test_chat_completions_request_and_diagnostics_contract(self) -> None:
        proposer = self._proposer()
        result = proposer({"prompt": "seed"}, {"train": [{"score": 1}]}, ["prompt"], metadata={"turn": 1})

        self.assertEqual(result, {"prompt": "improved"})
        self.assertEqual(len(self.calls), 1)
        request, timeout = self.calls[0]
        self.assertEqual(timeout, 2.0)
        self.assertEqual(request.full_url, "https://llm.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer explicit-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "explicit-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("seed", body["messages"][1]["content"])

        proposal_dir = proposer.last_proposal_dir
        assert proposal_dir is not None
        self.assertEqual(json.loads((proposal_dir / "response.json").read_text()), {"new_texts": {"prompt": "improved"}, "summary": "better"})
        self.assertEqual(json.loads((proposal_dir / "usage.json").read_text())["input_tokens"], 4)
        self.assertTrue((proposal_dir / "chat_completion_response.json").exists())

    def test_environment_configuration_is_authoritative(self) -> None:
        os.environ.update(
            {
                "OPENAI_BASE_URL": "https://env.example/v1",
                "OPENAI_MODEL": "env-model",
                "OPENAI_API_KEY": "env-key",
            }
        )
        proposer = self._proposer()
        proposer({"prompt": "seed"}, {}, ["prompt"])
        request, _ = self.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://env.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer env-key")
        self.assertEqual(body["model"], "env-model")

    def test_missing_environment_or_explicit_configuration_fails_closed(self) -> None:
        proposer = CodexAgentProposer(self.run_dir, sandbox=True, opener=self._opener)
        with self.assertRaisesRegex(CodexProcessError, "OPENAI_BASE_URL"):
            proposer({"prompt": "seed"}, {}, ["prompt"])

    def test_malformed_response_preserves_error_diagnostics(self) -> None:
        self.payload = {"choices": [{"message": {"content": "not structured"}}]}
        proposer = self._proposer()
        with self.assertRaises(ProposalValidationError) as caught:
            proposer({"prompt": "seed"}, {}, ["prompt"])
        proposal_dir = caught.exception.proposal_dir
        assert proposal_dir is not None
        self.assertIn("new_texts", (proposal_dir / "error.txt").read_text())

    def test_extra_component_is_rejected(self) -> None:
        self.payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"new_texts": {"prompt": "ok", "extra": "bad"}, "summary": None}
                        )
                    }
                }
            ]
        }
        with self.assertRaisesRegex(ProposalValidationError, "extra"):
            self._proposer()({"prompt": "seed"}, {}, ["prompt"])

    def test_timeout_is_distinct_from_validation_failure(self) -> None:
        def timeout_opener(_request: object, timeout: float) -> FakeResponse:
            raise TimeoutError(f"timed out after {timeout}")

        proposer = self._proposer(opener=timeout_opener)
        with self.assertRaises(CodexProposalTimeout):
            proposer({"prompt": "seed"}, {}, ["prompt"])

    def test_http_failure_is_wrapped_without_exposing_api_key(self) -> None:
        def error_opener(_request: object, _timeout: float) -> FakeResponse:
            raise HTTPError("https://llm.example/v1/chat/completions", 401, "unauthorized", {}, None)

        proposer = self._proposer(api_key="do-not-print", opener=error_opener)
        with self.assertRaises(CodexProcessError) as caught:
            proposer({"prompt": "seed"}, {}, ["prompt"])
        self.assertNotIn("do-not-print", str(caught.exception))

    def test_cost_budget_uses_chat_usage_rates(self) -> None:
        self.payload = {
            "choices": [{"message": {"content": json.dumps({"new_texts": {"prompt": "ok"}, "summary": None})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
        }
        proposer = self._proposer(input_cost_per_million=1.0, output_cost_per_million=1.0, max_token_cost=0.0001)
        with self.assertRaises(CodexTokenBudgetExceeded):
            proposer({"prompt": "seed"}, {}, ["prompt"])
        self.assertAlmostEqual(proposer.total_cost, 0.0002)

    def test_sandbox_and_external_workspace_contract_remains_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "sandbox must be True"):
            CodexAgentProposer(self.run_dir, sandbox=False)
        with self.assertRaisesRegex(ValueError, "outside the development checkout"):
            CodexAgentProposer(Path(__file__).resolve().parents[1] / "runs")


if __name__ == "__main__":
    unittest.main()
