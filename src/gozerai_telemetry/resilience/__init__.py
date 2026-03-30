"""Resilience patterns for production telemetry.

Includes base patterns (retry, circuit breaker, resilient HTTP) plus
advanced patterns (adaptive timeout, hedged requests, load shedding).

Zero external dependencies.
"""

# Base resilience -- import everything from _base so mock patching
# of gozerai_telemetry.resilience.urlopen still works
from gozerai_telemetry.resilience._base import *  # noqa: F401,F403

# Advanced resilience patterns
from gozerai_telemetry.resilience.adaptive_timeout import AdaptiveTimeout
from gozerai_telemetry.resilience.hedged_request import HedgedRequest, HedgedResult
from gozerai_telemetry.resilience.load_shedding import LoadShedder, ShedDecision

__all__ = [
    # Base
    "CircuitBreaker",
    "CircuitState",
    "RetryPolicy",
    "DEFAULT_RETRY",
    "CONSERVATIVE_RETRY",
    "AGGRESSIVE_RETRY",
    "get_circuit_breaker",
    "reset_all_breakers",
    "resilient_fetch",
    "resilient_request",
    # Advanced
    "AdaptiveTimeout",
    "HedgedRequest",
    "HedgedResult",
    "LoadShedder",
    "ShedDecision",
]
