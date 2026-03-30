"""Autonomous circuit breaker threshold tuning from failure patterns.

Learns from failure patterns to dynamically adjust failure_threshold
and recovery_timeout for circuit breakers.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

from gozerai_telemetry.resilience import CircuitBreaker, CircuitState


@dataclass
class _FailureRecord:
    timestamp: float
    recovered: bool = False
    recovery_time: float = 0.0


class CircuitBreakerTuner:
    """Automatically tunes circuit breaker parameters based on failure patterns.

    Tracks failure bursts and recovery times to recommend optimal
    failure_threshold and recovery_timeout values.

    Usage:
        tuner = CircuitBreakerTuner()
        cb = CircuitBreaker(name="api", failure_threshold=5)
        tuner.attach(cb)
        # ... use cb normally ...
        tuner.record_failure("api")
        tuner.record_success("api")
        recommendation = tuner.get_recommendation("api")
    """

    def __init__(
        self,
        history_size: int = 500,
        min_failures_to_tune: int = 5,
        burst_window: float = 10.0,
    ) -> None:
        self.history_size = history_size
        self.min_failures_to_tune = min_failures_to_tune
        self.burst_window = burst_window
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._failures: Dict[str, Deque[_FailureRecord]] = {}
        self._recovery_times: Dict[str, Deque[float]] = {}
        self._trip_times: Dict[str, List[float]] = {}
        self._lock = Lock()

    def attach(self, cb: CircuitBreaker) -> None:
        """Attach a circuit breaker for autonomous tuning."""
        with self._lock:
            self._breakers[cb.name] = cb
            if cb.name not in self._failures:
                self._failures[cb.name] = deque(maxlen=self.history_size)
                self._recovery_times[cb.name] = deque(maxlen=100)
                self._trip_times[cb.name] = []

    def record_failure(self, name: str) -> None:
        """Record a failure event for a circuit breaker."""
        now = time.time()
        with self._lock:
            if name not in self._failures:
                self._failures[name] = deque(maxlen=self.history_size)
                self._recovery_times[name] = deque(maxlen=100)
                self._trip_times[name] = []
            self._failures[name].append(_FailureRecord(timestamp=now))

            # Detect if circuit just tripped
            cb = self._breakers.get(name)
            if cb and cb.state == CircuitState.OPEN:
                self._trip_times.setdefault(name, []).append(now)

    def record_success(self, name: str) -> None:
        """Record a recovery/success event."""
        now = time.time()
        with self._lock:
            if name not in self._failures:
                return

            # If there are unrecovered failures, mark the most recent as recovered
            trip_times = self._trip_times.get(name, [])
            if trip_times:
                last_trip = trip_times[-1]
                recovery_time = now - last_trip
                if name not in self._recovery_times:
                    self._recovery_times[name] = deque(maxlen=100)
                self._recovery_times[name].append(recovery_time)
                trip_times.pop()

    def _count_bursts(self, name: str) -> List[int]:
        """Count failure bursts (clusters within burst_window)."""
        with self._lock:
            failures = list(self._failures.get(name, []))

        if not failures:
            return []

        bursts = []
        current_burst = 1
        for i in range(1, len(failures)):
            if failures[i].timestamp - failures[i - 1].timestamp <= self.burst_window:
                current_burst += 1
            else:
                bursts.append(current_burst)
                current_burst = 1
        bursts.append(current_burst)
        return bursts

    def get_recommendation(self, name: str) -> Dict[str, Any]:
        """Get tuning recommendation for a circuit breaker.

        Returns recommended failure_threshold and recovery_timeout based
        on observed patterns.
        """
        with self._lock:
            failures = list(self._failures.get(name, []))
            recovery_times = list(self._recovery_times.get(name, []))
            cb = self._breakers.get(name)

        if len(failures) < self.min_failures_to_tune:
            current_threshold = cb.failure_threshold if cb else 5
            current_timeout = cb.recovery_timeout if cb else 60.0
            return {
                "failure_threshold": current_threshold,
                "recovery_timeout": current_timeout,
                "confidence": 0.0,
                "reason": "insufficient_data",
                "failure_count": len(failures),
            }

        bursts = self._count_bursts(name)
        if not bursts:
            bursts = [len(failures)]

        # Recommended threshold: median burst size + 1 (to avoid tripping on small bursts)
        sorted_bursts = sorted(bursts)
        median_idx = len(sorted_bursts) // 2
        median_burst = sorted_bursts[median_idx]
        recommended_threshold = max(3, min(median_burst + 1, 20))

        # Recommended recovery timeout: based on observed recovery times
        if recovery_times:
            sorted_recovery = sorted(recovery_times)
            p75_idx = int(len(sorted_recovery) * 0.75)
            p75_recovery = sorted_recovery[min(p75_idx, len(sorted_recovery) - 1)]
            recommended_timeout = max(10.0, min(p75_recovery * 1.5, 300.0))
        else:
            recommended_timeout = 60.0

        confidence = min(1.0, len(failures) / (self.min_failures_to_tune * 5))

        return {
            "failure_threshold": recommended_threshold,
            "recovery_timeout": round(recommended_timeout, 1),
            "confidence": round(confidence, 2),
            "reason": "pattern_analysis",
            "failure_count": len(failures),
            "burst_sizes": bursts,
            "avg_recovery_time": round(sum(recovery_times) / len(recovery_times), 1) if recovery_times else None,
        }

    def apply_recommendation(self, name: str) -> bool:
        """Apply tuning recommendation to the attached circuit breaker."""
        rec = self.get_recommendation(name)
        if rec["confidence"] < 0.3:
            return False

        with self._lock:
            cb = self._breakers.get(name)
        if cb is None:
            return False

        cb.failure_threshold = rec["failure_threshold"]
        cb.recovery_timeout = rec["recovery_timeout"]
        return True

    def get_all_recommendations(self) -> Dict[str, Dict[str, Any]]:
        """Get recommendations for all tracked circuit breakers."""
        with self._lock:
            names = list(self._failures.keys())
        return {name: self.get_recommendation(name) for name in names}
