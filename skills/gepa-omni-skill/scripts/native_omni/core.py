"""Task, evaluation, budget, and result primitives for native Omni engines.

Adapted from the MIT-licensed GEPA project at commit
8a2bed96385202f69caaeb5327a843ed2f5ea225.  This module is a deliberately
small stdlib-only implementation and imports neither GEPA nor plugin sources.
"""

from __future__ import annotations

import functools
import inspect
import json
import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class BudgetExhausted(RuntimeError):
    """Raised when an evaluation would exceed the configured eval budget."""


@dataclass
class BudgetTracker:
    """Thread-safe ledger for individual example evaluations."""

    max_evals: int | None = None
    _used: int = field(default=0, init=False, repr=False)
    _reserved: int = field(default=0, init=False, repr=False)
    _log: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_evals is not None and (
            isinstance(self.max_evals, bool) or not isinstance(self.max_evals, int) or self.max_evals < 0
        ):
            raise ValueError("max_evals must be a non-negative integer or None")

    def check(self, count: int = 1) -> None:
        """Ensure capacity remains, including evaluations currently in flight."""
        if count < 0:
            raise ValueError("count must not be negative")
        with self._lock:
            if self.max_evals is not None and self._used + self._reserved + count > self.max_evals:
                raise BudgetExhausted(f"Eval budget exhausted: {self._used}/{self.max_evals} used")

    def reserve(self, count: int = 1) -> None:
        """Atomically reserve capacity before an evaluator can be invoked."""
        if count < 0:
            raise ValueError("count must not be negative")
        with self._lock:
            if self.max_evals is not None and self._used + self._reserved + count > self.max_evals:
                raise BudgetExhausted(f"Eval budget exhausted: {self._used}/{self.max_evals} used")
            self._reserved += count

    def release(self, count: int = 1) -> None:
        """Return unused reservations after a failed evaluator invocation."""
        if count < 0:
            raise ValueError("count must not be negative")
        with self._lock:
            if count > self._reserved:
                raise RuntimeError("cannot release more evaluation capacity than reserved")
            self._reserved -= count

    def commit(self, score: float) -> int:
        """Settle one reservation after a successful evaluator invocation."""
        with self._lock:
            if not self._reserved:
                raise RuntimeError("cannot record an evaluation without a reservation")
            self._reserved -= 1
            self._used += 1
            self._log.append({"eval": self._used, "score": float(score), "time": time.time()})
            return self._used

    def record(self, score: float) -> int:
        """Reserve and immediately settle one synchronous evaluation."""
        self.reserve()
        return self.commit(score)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int | None:
        with self._lock:
            return None if self.max_evals is None else self.max_evals - self._used - self._reserved

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0 if self.max_evals is not None else False

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"used": self.used, "exhausted": self.exhausted}
        if self.max_evals is not None:
            result.update(max_evals=self.max_evals, remaining_evals=self.remaining)
        return result


def _as_list(value: Any, *, label: str) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{label} must be a sequence of examples or None")
    return list(value)


@dataclass(frozen=True)
class Task:
    """Normalized single-string-candidate task for native engine execution."""

    name: str = "task"
    seed_candidate: str = ""
    evaluator: Callable[..., Any] | None = None
    batch_evaluator: Callable[..., Any] | None = None
    dataset: list[Any] | None = None
    valset: list[Any] | None = None
    test_set: list[Any] | None = None
    objective: str = ""
    background: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("task name must be a non-empty string")
        if not isinstance(self.seed_candidate, str):
            raise TypeError("native engines accept only a string seed_candidate")
        if self.evaluator is None and self.batch_evaluator is None:
            raise ValueError("task requires evaluator or batch_evaluator")
        if self.evaluator is not None and not callable(self.evaluator):
            raise TypeError("evaluator must be callable")
        if self.batch_evaluator is not None and not callable(self.batch_evaluator):
            raise TypeError("batch_evaluator must be callable")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, seed_candidate: str | None = None) -> "Task":
        """Normalize the plugin's existing task mapping without exposing test data."""
        if not isinstance(raw, Mapping):
            raise TypeError("task must be a mapping")
        candidate = raw.get("seed_candidate", "") if seed_candidate is None else seed_candidate
        if not isinstance(candidate, str):
            raise TypeError("native engines accept only a string candidate")
        return cls(
            name=str(raw.get("name", "task")),
            seed_candidate=candidate,
            evaluator=raw.get("evaluator"),
            batch_evaluator=raw.get("batch_evaluator"),
            dataset=_as_list(raw.get("dataset"), label="dataset"),
            valset=_as_list(raw.get("valset"), label="valset"),
            test_set=_as_list(raw.get("test_set"), label="test_set"),
            objective=str(raw.get("objective", "")),
            background=str(raw.get("background", "")),
        )

    @property
    def has_dataset(self) -> bool:
        return self.dataset is not None or self.valset is not None

    def agent_metadata(self) -> dict[str, Any]:
        """Return safe HTTP/task metadata; held-out examples are never included."""
        return {
            "name": self.name,
            "seed_candidate": self.seed_candidate,
            "objective": self.objective,
            "background": self.background,
            "train_count": len(self.dataset or []),
            "val_count": len(self.valset or []),
        }


def _normalize_result(result: Any) -> tuple[float, dict[str, Any]]:
    if isinstance(result, (tuple, list)):
        if not result:
            raise ValueError("evaluator returned an empty result")
        score = result[0]
        info = result[1] if len(result) > 1 else None
    else:
        score, info = result, None
    try:
        normalized_score = float(score)
    except (TypeError, ValueError) as exc:
        raise TypeError("evaluator score must be numeric") from exc
    if not math.isfinite(normalized_score):
        raise ValueError("evaluator score must be finite")
    if info is None:
        return normalized_score, {}
    if not isinstance(info, Mapping):
        raise TypeError("evaluator info must be a mapping")
    return normalized_score, dict(info)


def _accepted_kwargs(fn: Callable[..., Any]) -> set[str] | None:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return set()
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return None
    return set(signature.parameters)


def normalize_evaluator(fn: Callable[..., Any]) -> Callable[..., tuple[float, dict[str, Any]]]:
    """Normalize scalar and ``(score, info)`` evaluator outputs."""
    if not callable(fn):
        raise TypeError("evaluator must be callable")
    accepted = _accepted_kwargs(fn)

    @functools.wraps(fn)
    def evaluate(*args: Any, **kwargs: Any) -> tuple[float, dict[str, Any]]:
        if accepted is not None:
            kwargs = {name: value for name, value in kwargs.items() if name in accepted}
        return _normalize_result(fn(*args, **kwargs))

    return evaluate


def normalize_batch_evaluator(fn: Callable[..., Any]) -> Callable[..., list[tuple[float, dict[str, Any]]]]:
    """Normalize batch results and enforce one ordered result per input pair."""
    if not callable(fn):
        raise TypeError("batch_evaluator must be callable")
    accepted = _accepted_kwargs(fn)

    @functools.wraps(fn)
    def evaluate(pairs: list[tuple[str, Any]], **kwargs: Any) -> list[tuple[float, dict[str, Any]]]:
        call_kwargs = (
            kwargs if accepted is None else {name: value for name, value in kwargs.items() if name in accepted}
        )
        results = list(fn(list(pairs), **call_kwargs))
        if len(results) != len(pairs):
            raise ValueError(f"batch_evaluator returned {len(results)} results but expected {len(pairs)}")
        return [_normalize_result(value) for value in results]

    return evaluate


@dataclass(frozen=True)
class Result:
    """Serializable result value shared by the native optimization engines."""

    best_candidate: str
    best_score: float
    total_evals: int
    eval_log: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_candidate": self.best_candidate,
            "best_score": self.best_score,
            "total_evals": self.total_evals,
            "eval_log": self.eval_log,
            "metadata": self.metadata,
        }

    def persist(self, output_dir: str | Path) -> Path:
        directory = _external_output_dir(output_dir)
        target = directory / "result.json"
        target.write_text(json.dumps(self.to_dict(), indent=2, default=str) + "\n", encoding="utf-8")
        return target


# ``NativeResult`` remains a descriptive alias for call sites that want to
# avoid colliding with third-party result classes; ``Result`` is the primary
# engine-facing name.
NativeResult = Result


def _runtime_checkout() -> Path:
    return Path(__file__).resolve().parents[4]


def _external_output_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if not path.is_absolute():
        raise ValueError("output_dir must be an absolute external path")
    resolved = path.resolve()
    try:
        resolved.relative_to(_runtime_checkout())
    except ValueError:
        pass
    else:
        raise ValueError("output_dir must be outside the plugin checkout")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _example_id(item: Any, fallback: str) -> str:
    if isinstance(item, Mapping) and "id" in item:
        return str(item["id"])
    if hasattr(item, "id"):
        return str(item.id)
    return fallback


class EvalServer:
    """Budgeted in-process evaluator plus a loopback-only train/val HTTP API."""

    def __init__(self, task: Task, budget: BudgetTracker, *, output_dir: str | Path | None = None) -> None:
        self.task = task
        self.budget = budget
        self.eval_fn = normalize_evaluator(task.evaluator) if task.evaluator is not None else None
        self.batch_fn = normalize_batch_evaluator(task.batch_evaluator) if task.batch_evaluator is not None else None
        self.output_dir = _external_output_dir(output_dir) if output_dir is not None else None
        self._examples: dict[str, Any] = {}
        self._split_ids: dict[str, list[str]] = {"train": [], "val": []}
        self._log: list[dict[str, Any]] = []
        self._best_candidate = task.seed_candidate
        self._best_score = float("-inf")
        self._lock = threading.Lock()
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        for split, examples in (("train", task.dataset), ("val", task.valset)):
            for index, item in enumerate(examples or []):
                example_id = _example_id(item, f"{split}_{index}")
                if example_id in self._examples:
                    example_id = f"{split}_{index}"
                self._examples[example_id] = item
                self._split_ids[split].append(example_id)

    @property
    def url(self) -> str:
        if self._http is None:
            raise RuntimeError("EvalServer is not running")
        host, port = self._http.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def eval_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def iter_split(self, split: str) -> Iterator[tuple[str, Any]]:
        if split not in self._split_ids:
            raise ValueError("split must be 'train' or 'val'")
        for example_id in self._split_ids[split]:
            yield example_id, self._examples[example_id]

    def _score_pair(self, candidate: str, example: Any | None) -> tuple[float, dict[str, Any]]:
        if not isinstance(candidate, str):
            raise TypeError("native engines accept only string candidates")
        if self.eval_fn is not None:
            return self.eval_fn(candidate, example) if example is not None else self.eval_fn(candidate)
        assert self.batch_fn is not None
        return self.batch_fn([(candidate, example)])[0]

    def _track(self, candidate: str, score: float, info: Mapping[str, Any], example_id: str | None) -> None:
        eval_number = self.budget.commit(score)
        entry = {
            "eval": eval_number,
            "candidate": candidate,
            "score": score,
            "info": dict(info),
            "example_id": example_id,
            "time": time.time(),
        }
        with self._lock:
            self._log.append(entry)
            if score > self._best_score:
                self._best_score, self._best_candidate = score, candidate
        self._persist_eval(entry)

    def _persist_eval(self, entry: Mapping[str, Any]) -> None:
        if self.output_dir is None:
            return
        eval_dir = self.output_dir / "evals"
        eval_dir.mkdir(exist_ok=True)
        (eval_dir / f"{entry['eval']}.json").write_text(
            json.dumps(entry, indent=2, default=str) + "\n", encoding="utf-8"
        )
        with (self.output_dir / "eval_trace.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps(entry, default=str) + "\n")
        self._persist_summary()

    def _persist_summary(self) -> None:
        if self.output_dir is None:
            return
        result = self.result()
        (self.output_dir / "summary.json").write_text(
            json.dumps(result.to_dict(), indent=2, default=str) + "\n", encoding="utf-8"
        )

    def evaluate(
        self, candidate: str, example: Any | None = None, *, example_id: str | None = None
    ) -> tuple[float, dict[str, Any]]:
        self.budget.reserve()
        try:
            score, info = self._score_pair(candidate, example)
        except Exception:
            self.budget.release()
            raise
        self._track(candidate, score, info, example_id)
        return score, {**info, "_budget": self.budget.status()}

    def evaluate_pairs(
        self, pairs: list[tuple[str, Any]], *, example_ids: list[str] | None = None
    ) -> list[tuple[float, dict[str, Any]]]:
        if not pairs:
            return []
        if example_ids is not None and len(example_ids) != len(pairs):
            raise ValueError("example_ids must align with pairs")
        self.budget.reserve(len(pairs))
        if self.batch_fn is None:
            results: list[tuple[float, dict[str, Any]]] = []
            settled = 0
            try:
                for index, (candidate, example) in enumerate(pairs):
                    score, info = self._score_pair(candidate, example)
                    self._track(candidate, score, info, (example_ids or [None] * len(pairs))[index])
                    settled += 1
                    results.append((score, {**info, "_budget": self.budget.status()}))
            except Exception:
                self.budget.release(len(pairs) - settled)
                raise
            return results
        try:
            results = self.batch_fn(pairs)
        except Exception:
            self.budget.release(len(pairs))
            raise
        for index, ((candidate, _example), (score, info)) in enumerate(zip(pairs, results, strict=True)):
            self._track(candidate, score, info, (example_ids or [None] * len(pairs))[index])
        return [(score, {**info, "_budget": self.budget.status()}) for score, info in results]

    def evaluate_split(self, candidate: str, split: str) -> tuple[float, dict[str, Any]]:
        if split not in self._split_ids:
            raise ValueError("split must be 'train' or 'val'")
        examples = list(self.iter_split(split))
        if not examples:
            return self.evaluate(candidate)
        results = self.evaluate_pairs(
            [(candidate, example) for _, example in examples], example_ids=[key for key, _ in examples]
        )
        scores = {key: score for (key, _), (score, _info) in zip(examples, results, strict=True)}
        return sum(scores.values()) / len(scores), {
            "scores": scores,
            "num_evaluated": len(scores),
            "_budget": self.budget.status(),
        }

    def score_heldout(self, candidate: str) -> tuple[float, dict[str, Any]] | None:
        """Score held-out examples in process only, outside the optimization budget."""
        if not self.task.test_set:
            return None
        pairs = [(candidate, example) for example in self.task.test_set]
        if self.batch_fn is not None:
            results = self.batch_fn(pairs)
        else:
            results = [self._score_pair(candidate, example) for _, example in pairs]
        scores = [score for score, _ in results]
        return sum(scores) / len(scores), {"scores": scores, "num_evaluated": len(scores)}

    def log_progress(self, candidate: str, val_score: float, **metadata: Any) -> dict[str, Any]:
        entry = {"candidate": candidate, "val_score": float(val_score), "time": time.time(), **metadata}
        if self.output_dir is not None:
            with (self.output_dir / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, default=str) + "\n")
        return entry

    def result(self, *, metadata: Mapping[str, Any] | None = None) -> Result:
        score = self._best_score if self._best_score != float("-inf") else 0.0
        merged_metadata = {"budget": self.budget.status(), **dict(metadata or {})}
        return Result(self._best_candidate, score, self.budget.used, self.eval_log, merged_metadata)

    def start(self) -> "EvalServer":  # noqa: C901 - compact HTTP handler keeps transport local to the server
        if self._http is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _send(self, status: int, body: Mapping[str, Any]) -> None:
                encoded = json.dumps(body, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                value = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(value, dict):
                    raise ValueError("request body must be an object")
                return value

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/task":
                    self._send(200, owner.task.agent_metadata())
                elif self.path == "/status":
                    self._send(200, {"budget": owner.budget.status(), "best_score": owner.result().best_score})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                try:
                    request = self._body()
                    candidate = request.get("candidate")
                    if not isinstance(candidate, str):
                        raise ValueError("candidate must be a string")
                    if self.path == "/evaluate":
                        split = request.get("split", "train")
                        if split not in {"train", "val"}:
                            self._send(404, {"error": "only train and val splits are available"})
                            return
                        score, info = owner.evaluate_split(candidate, split)
                    elif self.path == "/evaluate_examples":
                        split = request.get("split", "train")
                        if split not in {"train", "val"}:
                            self._send(404, {"error": "only train and val splits are available"})
                            return
                        ids = request.get("example_ids")
                        if ids is not None and (
                            not isinstance(ids, list) or any(not isinstance(key, str) for key in ids)
                        ):
                            raise ValueError("example_ids must be a list of strings")
                        pairs = [
                            (candidate, owner._examples[key])
                            for key in (ids or owner._split_ids[split])
                            if key in owner._split_ids[split]
                        ]
                        results = owner.evaluate_pairs(
                            pairs,
                            example_ids=[
                                key for key in (ids or owner._split_ids[split]) if key in owner._split_ids[split]
                            ],
                        )
                        score = sum(value[0] for value in results) / len(results) if results else 0.0
                        info = {"num_evaluated": len(results), "_budget": owner.budget.status()}
                    else:
                        self._send(404, {"error": "not found"})
                        return
                    self._send(200, {"score": score, "info": info, "budget": owner.budget.status()})
                except BudgetExhausted:
                    self._send(429, {"error": "budget exhausted", "budget": owner.budget.status()})
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception as exc:  # pragma: no cover - evaluator-specific failure surface
                    self._send(500, {"error": str(exc)})

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        if self._http is None:
            return
        self._http.shutdown()
        self._http.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._http = None
        self._thread = None

    def __enter__(self) -> "EvalServer":
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.close()
