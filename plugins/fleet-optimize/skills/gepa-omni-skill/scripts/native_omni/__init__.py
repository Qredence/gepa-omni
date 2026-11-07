"""Stdlib-only runtime primitives for the plugin-native Omni engines.

This package intentionally does not depend on the installed ``gepa`` package.
The optimization engines compose these primitives in a later integration step.
"""

from .core import (
    BudgetExhausted,
    BudgetTracker,
    EvalServer,
    NativeResult,
    Result,
    Task,
    normalize_batch_evaluator,
    normalize_evaluator,
)
from .chat_completions import ChatCompletionError, ChatCompletionResult, OpenAIChatCompletionsClient
from .runners import (
    AgentProcessError,
    AgentRunResult,
    AgentRunner,
    AgentTimeout,
    ClaudeAgentRunner,
    CodexAgentRunner,
    OpenAIChatCompletionRunner,
    PiAgentRunner,
    TokenBudgetExceeded,
)
from .autoresearch import AutoResearchConfig, AutoResearchEngine
from .best_of_n import BestOfNConfig, BestOfNEngine
from .coordinator import NATIVE_ENGINES, NativeBudget, run_native_engine, run_native_omni
from .meta_harness import MetaHarnessConfig, MetaHarnessEngine, validate_pending_candidates

__all__ = [
    "AgentProcessError",
    "AgentRunResult",
    "AgentRunner",
    "AgentTimeout",
    "AutoResearchConfig",
    "AutoResearchEngine",
    "BestOfNConfig",
    "BestOfNEngine",
    "BudgetExhausted",
    "BudgetTracker",
    "ChatCompletionError",
    "ChatCompletionResult",
    "ClaudeAgentRunner",
    "CodexAgentRunner",
    "EvalServer",
    "NativeResult",
    "NATIVE_ENGINES",
    "NativeBudget",
    "MetaHarnessConfig",
    "MetaHarnessEngine",
    "PiAgentRunner",
    "OpenAIChatCompletionRunner",
    "OpenAIChatCompletionsClient",
    "Result",
    "Task",
    "TokenBudgetExceeded",
    "normalize_batch_evaluator",
    "normalize_evaluator",
    "run_native_engine",
    "run_native_omni",
    "validate_pending_candidates",
]
