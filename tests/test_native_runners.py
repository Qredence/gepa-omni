from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

# fmt: off
SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "fleet-optimize"
    / "skills"
    / "gepa-omni-skill"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))
from codex_agent_proposer import CodexAgentProposer  # noqa: E402
from native_omni import (  # noqa: E402
    AgentProcessError,
    AgentTimeout,
    ChatCompletionError,
    ClaudeAgentRunner,
    CodexAgentRunner,
    OpenAIChatCompletionRunner,
    OpenAIChatCompletionsClient,
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
        with patch.dict(os.environ, {"GEPA_OMNI_RETAIN_RAW_RESPONSES": "1"}):
            first = runner.run("first", work_dir=self.work_dir)
            second = runner.run("second", work_dir=self.work_dir)

        self.assertEqual(first.command, ("openai-chat-completions", "https://llm.example/v1/chat/completions"))
        self.assertEqual((first.input_tokens, first.output_tokens), (100, 50))
        self.assertIn("choices", first.stdout)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.requests[0].data.decode())["model"], "test-model")
        second_messages = json.loads(self.requests[1].data.decode())["messages"]
        self.assertEqual([message["content"] for message in second_messages], ["first", "candidate", "second"])
        self.assertEqual(second.metadata["backend"], "pi")
        self.assertTrue((self.work_dir / "chat_completion_response.json").exists())

    def test_raw_response_is_not_retained_by_default(self) -> None:
        result = self._runner().run("first", work_dir=self.work_dir)

        self.assertFalse((self.work_dir / "chat_completion_response.json").exists())
        self.assertFalse(result.metadata["raw_response_retained"])
        self.assertEqual(result.stdout, "")

    def test_raw_response_is_retained_on_stdout_when_enabled(self) -> None:
        with patch.dict(os.environ, {"GEPA_OMNI_RETAIN_RAW_RESPONSES": "1"}):
            result = self._runner().run("first", work_dir=self.work_dir)

        self.assertTrue((self.work_dir / "chat_completion_response.json").exists())
        self.assertTrue(result.metadata["raw_response_retained"])
        self.assertIn("choices", result.stdout)

    def test_environment_values_override_compatibility_constructor_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_MODEL": "env-model",
                "OPENAI_API_KEY": "env-key",
            },
        ):
            self._runner(
                model="constructor-model", base_url="https://constructor.example/v1", api_key="constructor-key"
            ).run("go", work_dir=self.work_dir)
        request = self.requests[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer env-key")
        self.assertEqual(json.loads(request.data.decode())["model"], "env-model")

    def test_query_string_stays_on_the_chat_completions_url(self) -> None:
        client = OpenAIChatCompletionsClient(
            base_url="https://provider.example/v1?api-version=2024-10-21",
            model="provider-model",
            api_key="provider-key",
        )

        self.assertEqual(
            client.endpoint,
            "https://provider.example/v1/chat/completions?api-version=2024-10-21",
        )

    def test_redirect_does_not_move_the_bearer_token(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/stolen")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAIChatCompletionsClient(
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                model="local-model",
                api_key="secret",
            )
            with self.assertRaises(ChatCompletionError) as caught:
                client.complete([{"role": "user", "content": "hi"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("HTTP 302", str(caught.exception))

    def test_openai_key_is_rejected_for_a_non_openai_environment_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://llm.example/v1",
                "OPENAI_MODEL": "env-model",
                "OPENAI_API_KEY": "must-not-leave-openai",
            },
        ):
            with self.assertRaisesRegex(ValueError, "not permitted"):
                self._runner(api_key=None)

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
                self.assertEqual(result.metadata["runner_profile"], backend)

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

    def test_reused_raw_artifact_removed_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "work"
            directory.mkdir()
            artifact = directory / "chat_completion_response.json"
            with patch.dict(os.environ, {}, clear=True):
                for failure in (False, True):
                    artifact.write_text("sensitive")

                    def opener(*args, **kwargs):
                        if failure:
                            raise OSError("failure")
                        return FakeResponse()

                    runner = OpenAIChatCompletionRunner(
                        base_url="http://localhost/v1", model="model", api_key="key", opener=opener
                    )
                    if failure:
                        with self.assertRaises(AgentProcessError):
                            runner.run("prompt", work_dir=directory)
                    else:
                        runner.run("prompt", work_dir=directory)
                    self.assertFalse(artifact.exists())

    def test_provider_error_details_are_bounded_and_redacted(self):
        import io
        import urllib.error

        long_detail = b'{"api_key":"secret", "error":"invalid model ' + b"x" * 2000 + b'"}'
        errors = (
            urllib.error.HTTPError("http://localhost/v1", 400, "bad request", {}, io.BytesIO(long_detail)),
            urllib.error.URLError("TLS verification failed secret"),
        )
        with patch.dict(os.environ, {}, clear=True):
            for index, error in enumerate(errors):
                def opener(*args, **kwargs):
                    raise error

                client = OpenAIChatCompletionsClient(
                    base_url="http://localhost/v1", model="model", api_key="secret", opener=opener
                )
                with self.assertRaises(ChatCompletionError) as raised:
                    client.complete([{"role": "user", "content": "prompt"}])
                message = str(raised.exception)
                self.assertNotIn("secret", message)
                self.assertLessEqual(len(message), 600)
                if index == 0:
                    self.assertIn("invalid model", message)


if __name__ == "__main__":
    unittest.main()
