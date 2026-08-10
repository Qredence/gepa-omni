"""Stable public API for the GEPA Omni optimization plugin."""

from .api import OptimizationResult, optimize, run_optimization
from .config import OmniConfig
from .security import EndpointConfiguration, EndpointPolicyError, resolve_endpoint

__version__ = "0.3.0"

__all__ = [
    "EndpointConfiguration",
    "EndpointPolicyError",
    "OmniConfig",
    "OptimizationResult",
    "optimize",
    "resolve_endpoint",
    "run_optimization",
]
