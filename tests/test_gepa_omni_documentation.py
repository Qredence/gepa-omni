from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "gepa-omni"
    / "skills"
    / "gepa-omni-skill"
    / "SKILL.md"
)


def _python_blocks(markdown: str) -> list[str]:
    return re.findall(r"```python\s*\n(.*?)```", markdown, flags=re.DOTALL)


class DocumentationSnippetTests(unittest.TestCase):
    def test_every_skill_python_block_compiles_and_executes_with_local_fixtures(self) -> None:
        blocks = _python_blocks(SKILL.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(blocks), 3)

        fake_gepa_omni = types.ModuleType("gepa_omni")

        class FakeConfig:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        fake_gepa_omni.OmniConfig = FakeConfig
        fake_gepa_omni.optimize = lambda **_kwargs: object()

        fake_optimize_module = types.ModuleType("gepa.optimize_anything")
        fake_optimize_module.EngineConfig = FakeConfig
        fake_optimize_module.GEPAConfig = FakeConfig
        fake_optimize_module.ReflectionConfig = FakeConfig
        fake_optimize_module.optimize_anything = lambda **_kwargs: object()
        fake_gepa = types.ModuleType("gepa")
        fake_gepa.__path__ = []  # type: ignore[attr-defined]

        namespace = {
            "seed": "seed",
            "evaluate": lambda *_args: (1.0, {}),
            "train": ["train"],
            "validation": ["validation"],
            "test": ["test"],
            "model": "model",
            "SEED": "seed",
            "trainset": ["train"],
            "valset": ["validation"],
        }
        modules = {
            "gepa_omni": fake_gepa_omni,
            "gepa": fake_gepa,
            "gepa.optimize_anything": fake_optimize_module,
        }
        with patch.dict(sys.modules, modules):
            for index, source in enumerate(blocks, start=1):
                tree = ast.parse(source, filename=f"SKILL.md:block-{index}")
                compiled = compile(tree, f"SKILL.md:block-{index}", "exec")
                exec(compiled, namespace, namespace)  # noqa: S102 - documentation fixture execution


if __name__ == "__main__":
    unittest.main()
