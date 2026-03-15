"""Adaptive timeout that adjusts dynamically based on recent latency percentiles.

Tracks p50, p95, and p99 latencies from recent requests and calculates
timeouts as a multiple of observed latency. Prevents both premature timeouts
(too aggressive) and wasted time (too lenient).

Zero external dependencies.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Dict, List, Optional


@dataclass
class LatencySample:
    """A single latency observation."""
    duration: float  # seconds
    timestamp: float = field(default_factory=time.monotonic)
    success: bool = True


class AdaptiveTimeout:
    """Dynamically adjusts timeout based on observed latency percentiles.

    Maintains a sliding window of latency samples and computes percentile-based
    timeouts. The effective timeout is ``percentile_value * multiplier``,
    clamped between ``min_timeout`` and ``max_timeout``.

    Usage:
        at = AdaptiveTimeout(min_timeout=0.5, max_timeout=30.0)
        timeout = at.get_timeout()  # starts at initial_timeout
        # ... make request with timeout ...
        at.record(0.15)  # record observed latency
        timeout = at.get_timeout()  # now adapts to recent latencies
    """

    def __init__(
        self,
        min_timeout: float = 0.5,
        max_timeout: float = 30.0,
        initial_timeout: float = 5.0,
        multiplier: float = 3.0,
        percentile: float = 95.0,
        window_size: int = 100,
        window_seconds: float = 300.0,
        min_samples: int = 10,
    ) -> None:
        self.min_timeout = min_timeout
        self.max_timeout = max_timeout
        self.initial_timeout = initial_timeout
        self.multiplier = multiplier
        self.percentile = percentile
        self.window_size = window_size
        self.window_seconds = window_seconds
        self.min_samples = min_samples

        self._samples: Deque[LatencySample] = deque(maxlen=window_size)
        self._lock = Lock()
        self._total_samples = 0
        self._total_timeouts = 0

    def record(self, duration: float, success: bool = True) -> None:
        """Record an observed latency sample."""
        sample = LatencySample(duration=duration, success=success)
        with self._lock:
            self._samples.append(sample)
            self._total_samples += 1
            if not success:
                self._total_timeouts += 1

    def get_timeout(self) -> float:
        """Calculate the current adaptive timeout.

        Returns initial_timeout if there are not enough samples yet.
        Otherwise returns percentile * multiplier, clamped to [min, max].
        """
        with self._lock:
            samples = self._get_valid_samples()

        if len(samples) < self.min_samples:
            return self.initial_timeout

        durations = sorted(s.duration for s in samples)
        p_value = self._compute_percentile(durations, self.percentile)
        timeout = p_value * self.multiplier
        return max(self.min_timeout, min(self.max_timeout, timeout))

    def get_percentiles(self) -> Dict[str, float]:
        """Return p50, p95, and p99 of current samples."""
        with self._lock:
            samples = self._get_valid_samples()

        if not samples:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        durations = sorted(s.duration for s in samples)
        return {
            "p50": self._compute_percentile(durations, 50.0),
            "p95": self._compute_percentile(durations, 95.0),
            "p99": self._compute_percentile(durations, 99.0),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return current state and statistics."""
        with self._lock:
            samples = self._get_valid_samples()
            total = self._total_samples
            timeouts = self._total_timeouts

        percentiles: Dict[str, float] = {}
        if samples:
            durations = sorted(s.duration for s in samples)
            percentiles = {
                "p50": self._compute_percentile(durations, 50.0),
                "p95": self._compute_percentile(durations, 95.0),
                "p99": self._compute_percentile(durations, 99.0),
            }

        return {
            "current_timeout": self.get_timeout(),
            "sample_count": len(samples),
            "total_samples": total,
            "total_timeouts": timeouts,
            "min_timeout": self.min_timeout,
            "max_timeout": self.max_timeout,
            "multiplier": self.multiplier,
            "percentile": self.percentile,
            "percentiles": percentiles,
        }

    def reset(self) -> None:
        """Clear all recorded samples."""
        with self._lock:
            self._samples.clear()

    def _get_valid_samples(self) -> List[LatencySample]:
        """Return samples within the time window (must be called under lock)."""
        cutoff = time.monotonic() - self.window_seconds
        return [s for s in self._samples if s.timestamp >= cutoff]

    @staticmethod
    def _compute_percentile(sorted_values: List[float], percentile: float) -> float:
        """Compute percentile from a sorted list of values."""
        if not sorted_values:
            return 0.0
        n = len(sorted_values)
        if n == 1:
            return sorted_values[0]
        # Linear interpolation
        k = (percentile / 100.0) * (n - 1)
        f = int(k)
        c = f + 1
        if c >= n:
            return sorted_values[-1]
        d = k - f
        return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])
