from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# fmt: off
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
from codex_agent_proposer import (  # noqa: E402
    CodexProcessError,
    CodexProposalError,
    CodexProposalTimeout,
    ProposalValidationError,
)
from pi_agent_proposer import (  # noqa: E402
    PiAgentProposer,
    PiProcessError,
    PiProposalError,
    PiProposalTimeout,
    PiProposalValidationError,
)
# fmt: on


class FakeResponse:
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {"message": {"content": json.dumps({"new_texts": {"prompt": "pi-compatible"}, "summary": None})}}
                ]
            }
        ).encode()


class PiAgentProposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_api_env = {
            name: os.environ.get(name) for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")
        }
        for name in self._saved_api_env:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self._saved_api_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_pi_label_uses_the_same_chat_completions_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            requests: list[object] = []

            def opener(request: object, *, timeout: float) -> FakeResponse:
                del timeout
                requests.append(request)
                return FakeResponse()

            proposer = PiAgentProposer(
                Path(temporary),
                model="pi-compatible-model",
                base_url="https://llm.example/v1",
                api_key="test-key",
                opener=opener,
                pi_command="ignored-pi",
                sandbox=True,
            )
            self.assertEqual(proposer({"prompt": "seed"}, {}, ["prompt"]), {"prompt": "pi-compatible"})
            self.assertEqual(requests[0].full_url, "https://llm.example/v1/chat/completions")

    def test_historical_pi_exception_exports_are_preserved(self) -> None:
        self.assertIs(PiProposalError, CodexProposalError)
        self.assertIs(PiProposalValidationError, ProposalValidationError)
        self.assertIs(PiProcessError, CodexProcessError)
        self.assertIs(PiProposalTimeout, CodexProposalTimeout)


if __name__ == "__main__":
    unittest.main()
