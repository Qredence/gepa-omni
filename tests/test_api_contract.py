from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "gepa-omni-skill"
REFERENCE_ROOT = SKILL_ROOT / "references"


class ApiContractDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        cls.contributing = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        cls.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.api = (REFERENCE_ROOT / "api.md").read_text(encoding="utf-8")
        cls.codex = (REFERENCE_ROOT / "codex.md").read_text(encoding="utf-8")
        cls.pi = (REFERENCE_ROOT / "pi.md").read_text(encoding="utf-8")
        cls.omni = (REFERENCE_ROOT / "omni.md").read_text(encoding="utf-8")
        cls.gotchas = (REFERENCE_ROOT / "gotchas.md").read_text(encoding="utf-8")
        cls.preflight = (SKILL_ROOT / "scripts" / "preflight.py").read_text(encoding="utf-8")
        cls.notice = (SKILL_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    def test_stale_external_source_install_guidance_is_absent(self) -> None:
        documents = (
            self.readme,
            self.contributing,
            self.skill,
            self.api,
            self.codex,
            self.pi,
            self.omni,
            self.gotchas,
            self.preflight,
        )
        for document in documents:
            lowered = document.lower()
            stale_tokens = ("z" + "ochory", "source" + "-install", "source" + "-only", "source" + "-backed")
            for stale in stale_tokens:
                self.assertNotIn(stale, lowered)

    def test_pypi_and_native_engine_boundary_is_documented(self) -> None:
        combined = "\n".join((self.readme, self.skill, self.api, self.omni))
        for document in (self.readme, self.skill, self.api, self.omni):
            self.assertIn("gepa==0.1.4", document)
        for expected in ("autoresearch", "meta_harness", "best_of_n", "plugin-native"):
            self.assertIn(expected, combined)
        self.assertIn("standalone reflective", self.readme.lower())
        self.assertIn("Published PyPI", self.api)

    def test_direct_pypi_configuration_and_heldout_boundary_are_documented(self) -> None:
        self.assertIn("GEPAConfig", self.api)
        self.assertIn("EngineConfig.max_metric_calls", self.api)
        self.assertIn("ReflectionConfig", self.api)
        self.assertIn("stop_callbacks", self.api)
        self.assertIn("no `test_set` parameter", self.api)
        self.assertIn('task["test_set"]', self.api)
        self.assertNotIn("OptimizeAnythingConfig", self.api)

    def test_backend_parameters_and_chat_configuration_are_documented(self) -> None:
        combined = "\n".join((self.readme, self.skill, self.api, self.codex, self.pi, self.omni))
        for parameter in (
            "agent_backend",
            "agent_model",
            "codex_model",
            "pi_model",
            "codex_command",
            "pi_command",
            "codex_input_cost_per_million",
            "codex_output_cost_per_million",
            "sandbox=True",
        ):
            self.assertIn(parameter, combined)
        for variable in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY"):
            self.assertIn(variable, combined)
            self.assertIn(variable, self.preflight)
        self.assertIn("Chat Completions", combined)
        self.assertNotIn("CLI authentication", self.preflight)

    def test_preflight_contract_mentions_chat_runtime_and_env_configuration(self) -> None:
        for expected in (
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "OPENAI_API_KEY",
            "Chat Completions",
            "native_omni",
        ):
            self.assertIn(expected, self.preflight)

    def test_interactive_setup_contract_is_secret_safe_and_process_scoped(self) -> None:
        for document in (self.skill, self.readme, self.api, self.codex, self.pi, self.omni, self.gotchas):
            self.assertIn("missing", document.lower())
            self.assertIn("process", document.lower())
        normalized_skill = " ".join(self.skill.lower().split())
        self.assertIn("never ask the user to paste `openai_api_key` into chat", normalized_skill)
        self.assertIn("do not write prompted values to `.env`", normalized_skill)
        self.assertIn("preflight remains non-interactive", normalized_skill)
        self.assertIn("secret manager", self.readme)
        self.assertIn("never requested", self.api)

    def test_mit_provenance_is_pinned_and_packaged(self) -> None:
        self.assertIn("MIT", self.notice)
        self.assertIn("8a2bed96385202f69caaeb5327a843ed2f5ea225", self.notice)
        self.assertIn("native_omni/core.py", self.notice)
        self.assertIn("native_omni/runners.py", self.notice)
        self.assertIn("THIRD_PARTY_NOTICES.md", self.readme)

    def test_upstream_semantic_baseline_remains_recorded(self) -> None:
        self.assertIn("8a2bed96385202f69caaeb5327a843ed2f5ea225", self.skill)


if __name__ == "__main__":
    unittest.main()
