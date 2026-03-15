"""Autonomous health threshold adjustment based on historical data.

Learns from past health check durations and success rates to automatically
adjust thresholds for degraded/unhealthy classification.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Dict, List, Optional


@dataclass
class _CheckHistory:
    """Tracks history for a single health check."""
    durations: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    successes: int = 0
    failures: int = 0
    last_check_time: float = 0.0


class HealthThresholdTuner:
    """Automatically adjusts health check thresholds based on historical data.

    Monitors health check durations and success rates. When a check's
    p95 duration drifts, the degraded/unhealthy thresholds are adjusted
    to avoid false positives while still catching real problems.

    Usage:
        tuner = HealthThresholdTuner()
        tuner.record_check("database", duration_ms=12.5, success=True)
        tuner.record_check("database", duration_ms=15.0, success=True)
        thresholds = tuner.get_thresholds("database")
        # {"degraded_ms": ..., "unhealthy_ms": ..., "min_success_rate": ...}
    """

    def __init__(
        self,
        history_size: int = 200,
        degraded_percentile: float = 0.90,
        unhealthy_percentile: float = 0.99,
        degraded_multiplier: float = 2.0,
        unhealthy_multiplier: float = 5.0,
        min_samples: int = 10,
    ) -> None:
        self.history_size = history_size
        self.degraded_percentile = degraded_percentile
        self.unhealthy_percentile = unhealthy_percentile
        self.degraded_multiplier = degraded_multiplier
        self.unhealthy_multiplier = unhealthy_multiplier
        self.min_samples = min_samples
        self._checks: Dict[str, _CheckHistory] = {}
        self._lock = Lock()

    def record_check(self, name: str, duration_ms: float, success: bool) -> None:
        """Record a health check result for threshold learning."""
        with self._lock:
            if name not in self._checks:
                self._checks[name] = _CheckHistory(
                    durations=deque(maxlen=self.history_size)
                )
            hist = self._checks[name]
            hist.durations.append(duration_ms)
            hist.last_check_time = time.time()
            if success:
                hist.successes += 1
            else:
                hist.failures += 1

    def _percentile(self, data: List[float], p: float) -> float:
        """Calculate the p-th percentile of sorted data."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)

    def get_thresholds(self, name: str) -> Dict[str, float]:
        """Get computed thresholds for a check.

        Returns default thresholds if insufficient data.
        """
        with self._lock:
            if name not in self._checks:
                return self._default_thresholds()
            hist = self._checks[name]
            durations = list(hist.durations)
            total = hist.successes + hist.failures
            success_rate = hist.successes / total if total > 0 else 1.0

        if len(durations) < self.min_samples:
            return self._default_thresholds()

        p_degraded = self._percentile(durations, self.degraded_percentile)
        p_unhealthy = self._percentile(durations, self.unhealthy_percentile)
        median = self._percentile(durations, 0.5)

        # Thresholds are multiples of the observed percentiles
        degraded_ms = max(p_degraded * self.degraded_multiplier, median * 3.0)
        unhealthy_ms = max(p_unhealthy * self.unhealthy_multiplier, median * 10.0)

        # Minimum success rate based on observed rate (with some slack)
        min_success_rate = max(0.5, success_rate - 0.1)

        return {
            "degraded_ms": round(degraded_ms, 2),
            "unhealthy_ms": round(unhealthy_ms, 2),
            "min_success_rate": round(min_success_rate, 4),
            "observed_p50_ms": round(median, 2),
            "observed_p90_ms": round(p_degraded, 2),
            "observed_p99_ms": round(p_unhealthy, 2),
            "sample_count": len(durations),
            "success_rate": round(success_rate, 4),
        }

    def _default_thresholds(self) -> Dict[str, float]:
        return {
            "degraded_ms": 1000.0,
            "unhealthy_ms": 5000.0,
            "min_success_rate": 0.95,
            "observed_p50_ms": 0.0,
            "observed_p90_ms": 0.0,
            "observed_p99_ms": 0.0,
            "sample_count": 0,
            "success_rate": 1.0,
        }

    def is_degraded(self, name: str, duration_ms: float) -> bool:
        """Check if a duration indicates degraded health."""
        thresholds = self.get_thresholds(name)
        return duration_ms >= thresholds["degraded_ms"]

    def is_unhealthy(self, name: str, duration_ms: float) -> bool:
        """Check if a duration indicates unhealthy status."""
        thresholds = self.get_thresholds(name)
        return duration_ms >= thresholds["unhealthy_ms"]

    def get_all_thresholds(self) -> Dict[str, Dict[str, float]]:
        """Get thresholds for all tracked checks."""
        with self._lock:
            names = list(self._checks.keys())
        return {name: self.get_thresholds(name) for name in names}

    def reset(self, name: Optional[str] = None) -> None:
        """Reset history for one or all checks."""
        with self._lock:
            if name is None:
                self._checks.clear()
            else:
                self._checks.pop(name, None)
