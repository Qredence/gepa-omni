from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# fmt: off
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
from codex_agent_proposer import CodexAgentProposer  # noqa: E402
from native_omni import (  # noqa: E402
    AgentProcessError,
    AgentTimeout,
    ClaudeAgentRunner,
    CodexAgentRunner,
    OpenAIChatCompletionRunner,
    PiAgentRunner,
    TokenBudgetExceeded,
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
                "choices": [{"message": {"content": "candidate"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        ).encode()


class NativeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tempdir.name) / "work"
        self.requests: list[object] = []
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
        self.tempdir.cleanup()

    def opener(self, request: object, *, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request)
        return FakeResponse()

    def _runner(self, **kwargs: object) -> OpenAIChatCompletionRunner:
        options: dict[str, object] = {
            "model": "test-model",
            "base_url": "https://llm.example/v1",
            "api_key": "test-key",
            "opener": self.opener,
        }
        options.update(kwargs)
        return OpenAIChatCompletionRunner(**options)

    def test_shared_runner_uses_chat_completions_and_retains_history(self) -> None:
        runner = self._runner(backend="pi")
        first = runner.run("first", work_dir=self.work_dir)
        second = runner.run("second", work_dir=self.work_dir)

        self.assertEqual(first.command, ("openai-chat-completions", "https://llm.example/v1/chat/completions"))
        self.assertEqual((first.input_tokens, first.output_tokens), (100, 50))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.requests[0].data.decode())["model"], "test-model")
        second_messages = json.loads(self.requests[1].data.decode())["messages"]
        self.assertEqual([message["content"] for message in second_messages], ["first", "candidate", "second"])
        self.assertEqual(second.metadata["backend"], "pi")
        self.assertTrue((self.work_dir / "chat_completion_response.json").exists())

    def test_environment_values_override_compatibility_constructor_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://env.example/v1",
                "OPENAI_MODEL": "env-model",
                "OPENAI_API_KEY": "env-key",
            },
        ):
            self._runner(
                model="constructor-model", base_url="https://constructor.example/v1", api_key="constructor-key"
            ).run("go", work_dir=self.work_dir)
        request = self.requests[0]
        self.assertEqual(request.full_url, "https://env.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer env-key")
        self.assertEqual(json.loads(request.data.decode())["model"], "env-model")

    def test_cost_cap_uses_provider_usage_and_rates(self) -> None:
        runner = self._runner(input_cost_per_million=2.0, output_cost_per_million=8.0)
        result = runner.run("go", work_dir=self.work_dir, max_token_cost=0.001)
        self.assertAlmostEqual(result.cost_usd or 0.0, 0.0006)
        self.assertAlmostEqual(runner.total_cost_usd, 0.0006)
        with self.assertRaisesRegex(TokenBudgetExceeded, "no token budget remains"):
            runner.run("again", work_dir=self.work_dir, max_token_cost=0.001, spent_token_cost=0.001)

    def test_cost_cap_requires_rates(self) -> None:
        runner = self._runner()
        with self.assertRaisesRegex(ValueError, "both input/output pricing"):
            runner.run("go", work_dir=self.work_dir, max_token_cost=1.0)

    def test_invalid_budget_is_rejected_before_api_call(self) -> None:
        runner = self._runner()
        for kwargs in (
            {"max_token_cost": -0.1},
            {"max_token_cost": float("nan")},
            {"spent_token_cost": -0.1},
            {"spent_token_cost": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "finite non-negative"):
                runner.run("go", work_dir=self.work_dir, **kwargs)
        self.assertEqual(self.requests, [])

    def test_timeout_and_provider_errors_are_mapped_to_runner_errors(self) -> None:
        def timeout_opener(_request: object, *, timeout: float) -> FakeResponse:
            del timeout
            raise TimeoutError("timed out")

        with self.assertRaises(AgentTimeout):
            self._runner(opener=timeout_opener).run("go", work_dir=self.work_dir)

        def failing_opener(_request: object, *, timeout: float) -> FakeResponse:
            del timeout
            raise OSError("offline")

        with self.assertRaises(AgentProcessError):
            self._runner(opener=failing_opener).run("go", work_dir=self.work_dir)

    def test_workspaces_must_be_external(self) -> None:
        runner = self._runner()
        with self.assertRaisesRegex(ValueError, "outside the plugin checkout"):
            runner.run("go", work_dir=Path(__file__).resolve().parents[1])

    def test_historical_backend_classes_are_api_compatibility_wrappers(self) -> None:
        for runner_class, backend in (
            (CodexAgentRunner, "codex"),
            (PiAgentRunner, "pi"),
            (ClaudeAgentRunner, "claude"),
        ):
            with self.subTest(runner=runner_class.__name__):
                runner = runner_class(
                    model="test-model",
                    base_url="https://llm.example/v1",
                    api_key="test-key",
                    opener=self.opener,
                )
                result = runner.run("go", work_dir=self.work_dir)
                self.assertEqual(result.metadata["backend"], backend)

    def test_cost_bounded_proposals_do_not_run_concurrently(self) -> None:
        active = 0
        maximum_active = 0
        state_lock = threading.Lock()

        class ProposalResponse:
            def __enter__(self) -> "ProposalResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps({"new_texts": {"prompt": "candidate"}, "summary": None})
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    }
                ).encode()

        def opener(_request: object, *, timeout: float) -> ProposalResponse:
            nonlocal active, maximum_active
            del timeout
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return ProposalResponse()

        from concurrent.futures import ThreadPoolExecutor

        proposer = CodexAgentProposer(
            self.work_dir,
            model="test-model",
            base_url="https://llm.example/v1",
            api_key="test-key",
            opener=opener,
            input_cost_per_million=1.0,
            output_cost_per_million=1.0,
            max_token_cost=0.00031,
            sandbox=True,
        )

        def call() -> str:
            try:
                proposer({"prompt": "seed"}, {}, ["prompt"])
            except Exception as error:  # noqa: BLE001 - assert budget behavior below
                return type(error).__name__
            return "success"

        with ThreadPoolExecutor(max_workers=3) as pool:
            outcomes = list(pool.map(lambda _index: call(), range(3)))

        self.assertEqual(maximum_active, 1)
        self.assertEqual(outcomes.count("success"), 2)
        self.assertEqual(outcomes.count("CodexTokenBudgetExceeded"), 1)


if __name__ == "__main__":
    unittest.main()
