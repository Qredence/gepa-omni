#!/usr/bin/env python3
"""Compatibility alias for the Chat Completions GEPA proposer.

The public pipeline still accepts ``agent_backend="pi"`` for compatibility,
but model calls use the same OpenAI-compatible endpoint and environment
configuration as every other backend.
"""

from __future__ import annotations

from codex_agent_proposer import (
    CodexAgentProposer,
    CodexProcessError,
    CodexProposalError,
    CodexProposalTimeout,
    CodexTokenBudgetExceeded,
    ProposalValidationError,
)

PiProposalError = CodexProposalError
PiProposalValidationError = ProposalValidationError
PiProcessError = CodexProcessError
PiProposalTimeout = CodexProposalTimeout


class PiAgentProposer(CodexAgentProposer):
    """Use Chat Completions while retaining the historical Pi class name."""

    def __init__(self, *args, pi_command: str = "pi", **kwargs):
        kwargs.setdefault("codex_command", pi_command)
        super().__init__(*args, **kwargs)


__all__ = [
    "PiAgentProposer",
    "PiProcessError",
    "PiProposalError",
    "PiProposalTimeout",
    "PiProposalValidationError",
    "CodexProcessError",
    "CodexProposalError",
    "CodexProposalTimeout",
    "CodexTokenBudgetExceeded",
    "ProposalValidationError",
]
