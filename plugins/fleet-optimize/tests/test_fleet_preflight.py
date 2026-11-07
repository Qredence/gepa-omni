from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

PREFLIGHT_PATH = Path(__file__).resolve().parents[1] / "skills" / "fleet-optimize-skill" / "scripts" / "preflight.py"
PROVIDER_VARIABLES = ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")


def _load_preflight() -> ModuleType:
    # The gepa-omni skill also ships a preflight.py, so load this one under its own module name.
    spec = importlib.util.spec_from_file_location("fleet_optimize_preflight", PREFLIGHT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def preflight() -> ModuleType:
    return _load_preflight()


def _set_provider_environment(monkeypatch: pytest.MonkeyPatch, *, omit: tuple[str, ...] = ()) -> None:
    for name in PROVIDER_VARIABLES:
        if name in omit:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, "https://api.openai.com/v1" if name == "OPENAI_BASE_URL" else "configured")


def test_preflight_passes_with_compatible_gepa_and_provider(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_provider_environment(monkeypatch)
    compatibility = {"compatible": True}

    with patch.object(preflight, "describe_compatibility", return_value=compatibility):
        exit_code = preflight.main([])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["ok"] is True
    assert report["problems"] == []
    assert report["gepa"] == compatibility
    assert report["provider"] == dict.fromkeys(PROVIDER_VARIABLES, True)


def test_preflight_reports_missing_provider_variables(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_provider_environment(monkeypatch, omit=("OPENAI_MODEL", "OPENAI_API_KEY"))
    monkeypatch.setenv("OPENAI_BASE_URL", "   ")

    with patch.object(preflight, "describe_compatibility", return_value={"compatible": True}):
        exit_code = preflight.main([])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["ok"] is False
    assert report["provider"] == dict.fromkeys(PROVIDER_VARIABLES, False)
    assert report["problems"] == ["missing provider variables: OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_API_KEY"]


def test_preflight_reports_incompatible_gepa(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_provider_environment(monkeypatch)
    error = preflight.FleetOptimizeCompatibilityError("upstream GEPA is too old")

    with patch.object(preflight, "describe_compatibility", side_effect=error):
        exit_code = preflight.main([])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["ok"] is False
    assert report["gepa"] == {"compatible": False, "error": "upstream GEPA is too old"}
    assert report["problems"] == ["upstream GEPA is too old"]


@pytest.mark.parametrize("url", ["https://[::1", "https://example.com:bad", "no-scheme", "https:///missing"])
def test_preflight_reports_invalid_url(preflight, monkeypatch, capsys, url):
    _set_provider_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", url)
    with patch.object(preflight, "describe_compatibility", return_value={"compatible": True}):
        assert preflight.main([]) == 1
    assert "resolvable HTTP(S)" in capsys.readouterr().out
