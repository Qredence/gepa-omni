from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch, create_autospec


SKILL = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "fleet-optimize"
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

        from gepa_omni import OmniConfig, optimize
        from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig, optimize_anything

        fake_gepa_omni.OmniConfig = create_autospec(OmniConfig)
        fake_gepa_omni.optimize = create_autospec(optimize)

        fake_optimize_module = types.ModuleType("gepa.optimize_anything")
        fake_optimize_module.EngineConfig = create_autospec(EngineConfig)
        fake_optimize_module.GEPAConfig = create_autospec(GEPAConfig)
        fake_optimize_module.ReflectionConfig = create_autospec(ReflectionConfig)
        fake_optimize_module.optimize_anything = create_autospec(optimize_anything)
        fake_gepa = types.ModuleType("gepa")
        fake_gepa.__path__ = []  # type: ignore[attr-defined]

        namespace = {
            "seed": "seed",
            "evaluate": lambda *_args: (1.0, {}),
            "train": ["train"],
            "validation": ["validation"],
            "test": ["test"],
        }
        modules = {
            "gepa_omni": fake_gepa_omni,
            "gepa": fake_gepa,
            "gepa.optimize_anything": fake_optimize_module,
        }
        with patch.dict(sys.modules, modules), patch.dict("os.environ", {"OPENAI_MODEL": "model"}):
            for index, source in enumerate(blocks, start=1):
                tree = ast.parse(source, filename=f"SKILL.md:block-{index}")
                compiled = compile(tree, f"SKILL.md:block-{index}", "exec")
                exec(compiled, namespace, namespace)  # noqa: S102 - documentation fixture execution


if __name__ == "__main__":
    unittest.main()
