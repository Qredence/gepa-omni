"""Runtime safety guards shared by the GEPA Omni adapters."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def require_sandbox(sandbox: bool) -> None:
    """Reject configuration that would run an agent outside the sandbox."""

    if sandbox is not True:
        raise ValueError("sandbox must be True; unsandboxed agent execution is not supported")


def validate_external_path(path: str | Path, *, label: str) -> Path:
    """Resolve a runtime path and reject paths inside the development checkout."""

    resolved = Path(path).expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError(f"{label} must be outside the development checkout")
    return resolved
