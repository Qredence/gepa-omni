from __future__ import annotations

from native_omni import Task


def test_shipped_runtime_keeps_held_out_examples_out_of_agent_metadata() -> None:
    task = Task.from_mapping(
        {
            "evaluator": lambda _candidate, _example: 1.0,
            "dataset": [{"id": "train", "text": "visible"}],
            "test_set": [{"id": "test", "secret": "hidden"}],
        },
        seed_candidate="seed",
    )

    metadata = task.agent_metadata()

    assert "test_set" not in metadata
    assert "hidden" not in str(metadata)
