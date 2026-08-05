from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "gepa-omni-skill"


class ApiContractDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.api = (SKILL_ROOT / "references" / "api.md").read_text(encoding="utf-8")
        cls.omni = (SKILL_ROOT / "references" / "omni.md").read_text(encoding="utf-8")
        cls.codex = (SKILL_ROOT / "references" / "codex.md").read_text(encoding="utf-8")
        cls.gotchas = (SKILL_ROOT / "references" / "gotchas.md").read_text(encoding="utf-8")

    def test_public_launcher_is_string_first(self) -> None:
        self.assertIn("seed_candidate: str | None = None", self.api)
        self.assertIn("`seed_candidate` is a **single string**", self.api)
        self.assertIn("compatibility extension", self.api)
        self.assertNotIn("seed_candidate: str | dict[str, str]", self.api)
        self.assertIn("seed_candidate=SEED_PROMPT", self.api)

    def test_dataset_valset_and_test_set_roles_are_distinct(self) -> None:
        for expected in (
            "Single-task",
            "Multi-task",
            "Generalization",
            "Optimize on `dataset`, select on `valset`",
            "`test_set` is separate from the modes and is reporting-only",
            "result.metadata[\"test_score\"]",
        ):
            self.assertIn(expected, self.api)

    def test_budget_and_engine_semantics_are_documented(self) -> None:
        for expected in (
            "max_evals",
            "max_token_cost",
            "stop_at_score",
            "15–20 × len(valset)",
            "engine_config` is strict",
            "`best_of_n`",
            "baseline",
            "optimize_adaptive_sequential",
        ):
            self.assertIn(expected, self.api)
        for expected in (
            "reward hacking",
            "selection bias",
            "stochastic",
            "saturated",
            "engine_config",
            "stop_at_score",
        ):
            self.assertIn(expected, self.skill.lower() + self.api.lower() + self.gotchas.lower())

    def test_codex_and_pi_extensions_keep_the_component_contract(self) -> None:
        for document in (self.skill, self.api, self.codex):
            self.assertIn("dict[str, str]", document)
            self.assertIn("components_to_update", document)
        self.assertIn("The public `optimize_anything` launcher is string-candidate-first", self.codex)
        self.assertIn("CodexAgentProposer", self.skill)
        self.assertIn("PiAgentProposer", self.skill)

    def test_omni_is_default_and_standalone_engines_are_explicit(self) -> None:
        for expected in (
            "Phase 1: optimize_best_of (parallel)",
            "Phase 2: fresh optimize_anything",
            "continuation_engine=\"autoresearch\"",
            "test_set` out of all three Phase 1 calls",
            "explicit `engine=\"gepa\"",
        ):
            self.assertIn(expected, self.omni)
        self.assertIn("Use Omni by default", self.skill)
        self.assertIn("fresh Phase 2 continuation", self.api)

    def test_upstream_baseline_is_recorded(self) -> None:
        self.assertIn("ba30ee24e8f63dfdb9e557ed8cfaaec7aa09a6df", self.skill)
        self.assertIn("ba30ee24e8f63dfdb9e557ed8cfaaec7aa09a6df", self.api)


if __name__ == "__main__":
    unittest.main()
