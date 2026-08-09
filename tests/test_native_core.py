from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "gepa-omni-skill" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
from native_omni import (  # noqa: E402
    BudgetExhausted,
    BudgetTracker,
    EvalServer,
    NativeResult,
    Task,
    normalize_batch_evaluator,
    normalize_evaluator,
    validate_pending_candidates,
)

sys.path.pop(0)


class NativeCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tempdir.name) / "output"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_task_normalization_keeps_heldout_out_of_agent_metadata(self) -> None:
        task = Task.from_mapping(
            {
                "name": "isolation",
                "evaluator": lambda _candidate, _example: 1.0,
                "dataset": [{"id": "train", "text": "visible"}],
                "valset": [{"id": "val", "text": "also visible"}],
                "test_set": [{"id": "test", "secret": "do-not-share"}],
                "objective": "score it",
            },
            seed_candidate="seed",
        )

        metadata = task.agent_metadata()
        self.assertNotIn("test_set", metadata)
        self.assertNotIn("do-not-share", json.dumps(metadata))
        self.assertEqual(task.test_set, [{"id": "test", "secret": "do-not-share"}])
        with self.assertRaisesRegex(TypeError, "string candidate"):
            Task.from_mapping({"evaluator": lambda _candidate: 0.0}, seed_candidate={"bad": "shape"})  # type: ignore[arg-type]

    def test_evaluator_scalar_batch_order_and_cardinality_validation(self) -> None:
        scalar = normalize_evaluator(lambda _candidate, _example: 0.75)
        self.assertEqual(scalar("candidate", "example"), (0.75, {}))

        batch = normalize_batch_evaluator(
            lambda pairs: [(index, {"item": pair[1]}) for index, pair in enumerate(pairs)]
        )
        self.assertEqual(
            batch([("a", "first"), ("b", "second")]), [(0.0, {"item": "first"}), (1.0, {"item": "second"})]
        )

        mismatched = normalize_batch_evaluator(lambda _pairs: [0.1])
        with self.assertRaisesRegex(ValueError, "expected 2"):
            mismatched([("a", 1), ("b", 2)])

    def test_budget_aggregate_and_heldout_scoring(self) -> None:
        task = Task.from_mapping(
            {
                "evaluator": lambda candidate, item: (float(len(candidate) + item), {"item": item}),
                "dataset": [1, 3],
                "valset": [5],
                "test_set": [7],
            },
            seed_candidate="x",
        )
        server = EvalServer(task, BudgetTracker(max_evals=3), output_dir=self.output_dir)

        score, info = server.evaluate_split("xx", "train")
        self.assertEqual(score, 4.0)
        self.assertEqual(info["scores"], {"train_0": 3.0, "train_1": 5.0})
        heldout = server.score_heldout("xx")
        self.assertEqual(heldout, (9.0, {"scores": [9.0], "num_evaluated": 1}))
        self.assertEqual(server.budget.used, 2)

        server.evaluate_split("xx", "val")
        with self.assertRaises(BudgetExhausted):
            server.evaluate("xx", 1)

    def test_atomic_reservation_prevents_concurrent_over_budget_evaluator_calls(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def evaluator(_candidate: str, _example: object) -> float:
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return 1.0

        server = EvalServer(Task.from_mapping({"evaluator": evaluator}), BudgetTracker(max_evals=1))
        outcomes: list[Exception | float] = []

        def evaluate() -> None:
            try:
                outcomes.append(server.evaluate("candidate", "example")[0])
            except Exception as error:  # noqa: BLE001 - assert the public failure type below
                outcomes.append(error)

        first = threading.Thread(target=evaluate)
        second = threading.Thread(target=evaluate)
        first.start()
        self.assertTrue(started.wait(timeout=2))
        second.start()
        second.join(timeout=2)
        release.set()
        first.join(timeout=2)

        self.assertEqual(calls, 1)
        self.assertEqual(outcomes.count(1.0), 1)
        self.assertEqual(sum(isinstance(value, BudgetExhausted) for value in outcomes), 1)

    def test_failed_batch_releases_its_reservation(self) -> None:
        def fail_batch(_pairs: list[tuple[str, object]]) -> list[float]:
            raise RuntimeError("provider failed")

        server = EvalServer(
            Task.from_mapping({"batch_evaluator": fail_batch}),
            BudgetTracker(max_evals=2),
        )
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            server.evaluate_pairs([("candidate", "a"), ("candidate", "b")])
        self.assertEqual(server.budget.remaining, 2)

    def test_loopback_http_rejects_test_split_and_never_returns_heldout(self) -> None:
        task = Task.from_mapping(
            {
                "evaluator": lambda _candidate, item: float(item["score"]),
                "dataset": [{"id": "train", "score": 0.5}],
                "test_set": [{"id": "test", "score": 1.0, "secret": "hidden"}],
            }
        )
        with EvalServer(task, BudgetTracker(max_evals=3), output_dir=self.output_dir) as server:
            task_response = urllib.request.urlopen(server.url + "/task").read().decode("utf-8")
            self.assertNotIn("hidden", task_response)
            request = urllib.request.Request(
                server.url + "/evaluate",
                data=json.dumps({"candidate": "x", "split": "test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 404)

    def test_result_and_trace_are_persisted_in_external_output_dir(self) -> None:
        task = Task.from_mapping({"evaluator": lambda _candidate, _item: (0.8, {"ok": True}), "dataset": ["one"]})
        server = EvalServer(task, BudgetTracker(max_evals=2), output_dir=self.output_dir)
        server.evaluate_split("winner", "train")
        server.log_progress("winner", 0.8, phase="test")
        result = server.result(metadata={"engine": "best_of_n"})
        result_path = result.persist(self.output_dir)

        self.assertEqual(result.best_candidate, "winner")
        self.assertEqual(result.total_evals, 1)
        self.assertTrue((self.output_dir / "evals" / "1.json").is_file())
        self.assertTrue((self.output_dir / "eval_trace.jsonl").is_file())
        self.assertTrue((self.output_dir / "progress.jsonl").is_file())
        self.assertEqual(json.loads(result_path.read_text())["metadata"]["engine"], "best_of_n")

    def test_result_value_object_is_serializable(self) -> None:
        result = NativeResult("best", 0.9, 2, [{"score": 0.9}], {"source": "test"})
        self.assertEqual(result.to_dict()["best_candidate"], "best")

    def test_inline_pending_candidate_rejects_symlink(self) -> None:
        work_dir = Path(self.tempdir.name) / "work"
        agents = work_dir / "agents"
        agents.mkdir(parents=True)
        outside = Path(self.tempdir.name) / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        (agents / "pending_candidate.txt").symlink_to(outside)

        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            validate_pending_candidates(
                work_dir,
                [{"name": "candidate", "candidate": "overwrite"}],
                max_candidates=1,
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_native_token_budget_requires_pricing_rates(self) -> None:
        from native_omni.coordinator import _validate_budget

        with self.assertRaisesRegex(ValueError, "requires both input/output pricing rates"):
            _validate_budget(1, 1.0)
