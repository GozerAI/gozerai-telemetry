"""Autonomous retry policy optimization based on success/failure patterns.

Learns from retry outcomes to recommend optimal retry parameters:
max_retries, base_delay, and max_delay.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

from gozerai_telemetry.resilience import RetryPolicy


@dataclass
class _RetryOutcome:
    """Records the outcome of a retry sequence."""
    timestamp: float
    attempts_used: int
    success: bool
    total_delay: float
    final_status: Optional[int] = None


class RetryOptimizer:
    """Optimizes retry policies based on observed outcomes.

    Tracks which attempt number typically succeeds, and how much delay
    is wasted on retries that never succeed. Recommends optimal parameters.

    Usage:
        optimizer = RetryOptimizer()
        optimizer.record_outcome("api-calls", attempts_used=2, success=True, total_delay=1.5)
        optimizer.record_outcome("api-calls", attempts_used=4, success=False, total_delay=15.0)
        rec = optimizer.get_recommendation("api-calls")
    """

    def __init__(self, history_size: int = 500, min_samples: int = 10) -> None:
        self.history_size = history_size
        self.min_samples = min_samples
        self._outcomes: Dict[str, Deque[_RetryOutcome]] = {}
        self._lock = Lock()

    def record_outcome(
        self,
        name: str,
        attempts_used: int,
        success: bool,
        total_delay: float = 0.0,
        final_status: Optional[int] = None,
    ) -> None:
        """Record the outcome of a retry sequence."""
        outcome = _RetryOutcome(
            timestamp=time.time(),
            attempts_used=attempts_used,
            success=success,
            total_delay=total_delay,
            final_status=final_status,
        )
        with self._lock:
            if name not in self._outcomes:
                self._outcomes[name] = deque(maxlen=self.history_size)
            self._outcomes[name].append(outcome)

    def get_recommendation(self, name: str) -> Dict[str, Any]:
        """Get optimized retry policy parameters.

        Analysis:
        - If most successes happen by attempt N, recommend max_retries = N
        - If retries rarely succeed, recommend fewer retries to save delay
        - Base delay is tuned based on typical successful retry delays
        """
        with self._lock:
            outcomes = list(self._outcomes.get(name, []))

        if len(outcomes) < self.min_samples:
            return {
                "max_retries": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "confidence": 0.0,
                "reason": "insufficient_data",
                "sample_count": len(outcomes),
            }

        successes = [o for o in outcomes if o.success]
        failures = [o for o in outcomes if not o.success]
        total = len(outcomes)
        success_rate = len(successes) / total

        # Find the attempt number that captures 90% of successes
        if successes:
            attempt_counts = sorted(s.attempts_used for s in successes)
            p90_idx = min(int(len(attempt_counts) * 0.9), len(attempt_counts) - 1)
            p90_attempts = attempt_counts[p90_idx]
            recommended_max_retries = max(1, p90_attempts)

            # Average delay for successful retries
            successful_delays = [s.total_delay for s in successes if s.attempts_used > 1]
            if successful_delays:
                avg_delay = sum(successful_delays) / len(successful_delays)
                avg_attempts = sum(s.attempts_used for s in successes) / len(successes)
                recommended_base_delay = max(0.1, avg_delay / max(1, avg_attempts - 1))
            else:
                recommended_base_delay = 1.0
        else:
            # No successes from retries -- minimize wasted effort
            recommended_max_retries = 1
            recommended_base_delay = 2.0

        # If retry success rate is very low, reduce retries
        retry_successes = [s for s in successes if s.attempts_used > 1]
        if retry_successes:
            retry_success_rate = len(retry_successes) / total
            if retry_success_rate < 0.05:
                recommended_max_retries = 1
        elif total > self.min_samples:
            recommended_max_retries = max(1, recommended_max_retries - 1)

        recommended_max_delay = min(60.0, recommended_base_delay * (2 ** recommended_max_retries))
        confidence = min(1.0, len(outcomes) / (self.min_samples * 5))

        return {
            "max_retries": recommended_max_retries,
            "base_delay": round(recommended_base_delay, 2),
            "max_delay": round(recommended_max_delay, 2),
            "confidence": round(confidence, 2),
            "reason": "pattern_analysis",
            "sample_count": len(outcomes),
            "success_rate": round(success_rate, 4),
            "retry_success_count": len(retry_successes) if successes else 0,
        }

    def create_policy(self, name: str) -> RetryPolicy:
        """Create an optimized RetryPolicy based on observed patterns."""
        rec = self.get_recommendation(name)
        return RetryPolicy(
            max_retries=rec["max_retries"],
            base_delay=rec["base_delay"],
            max_delay=rec["max_delay"],
        )

    def get_all_recommendations(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            names = list(self._outcomes.keys())
        return {name: self.get_recommendation(name) for name in names}
