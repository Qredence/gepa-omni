from __future__ import annotations

from check_gepa_omni_provenance import check


def test_gepa_omni_provenance_is_synchronized() -> None:
    assert check() == []


def test_provenance_requires_object(tmp_path, monkeypatch) -> None:
    import check_gepa_omni_provenance as checker

    path = tmp_path / "PROVENANCE.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(checker, "PROVENANCE_PATH", path)
    assert checker.check() == ["provenance must be an object"]


def test_provenance_negative_fixtures(tmp_path, monkeypatch):
    import json
    import check_gepa_omni_provenance as checker

    commit = "a" * 40
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "SKILL_ROOT", tmp_path)
    for constant, filename in (
        ("PROVENANCE_PATH", "PROVENANCE.json"),
        ("NOTICE_PATH", "NOTICE.md"),
        ("SKILL_PATH", "SKILL.md"),
    ):
        monkeypatch.setattr(checker, constant, tmp_path / filename)
    checker.PROVENANCE_PATH.write_text(json.dumps({"upstream": {"commit": commit}, "files": ["source.py"]}))
    checker.NOTICE_PATH.write_text(commit)
    checker.SKILL_PATH.write_text(commit)
    source = tmp_path / "source.py"
    source.write_text(commit)
    assert checker.check() == []
    source.unlink()
    assert any("missing" in e for e in checker.check())
    source.write_text("wrong revision")
    assert any("source header" in e for e in checker.check())
    source.write_text(commit)
    checker.NOTICE_PATH.unlink()
    checker.SKILL_PATH.unlink()
    assert len(checker.check()) == 2
    checker.PROVENANCE_PATH.write_text("{")
    assert checker.check()
    checker.PROVENANCE_PATH.unlink()
    assert checker.check()
