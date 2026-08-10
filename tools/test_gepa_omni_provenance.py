from __future__ import annotations

from check_gepa_omni_provenance import check


def test_gepa_omni_provenance_is_synchronized() -> None:
    assert check() == []
