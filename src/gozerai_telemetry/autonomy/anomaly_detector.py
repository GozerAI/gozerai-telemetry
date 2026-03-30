"""Anomaly detection in metrics with automatic alerting thresholds.

Uses statistical methods (z-score, moving average deviation) to detect
anomalous metric values. Zero external dependencies.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, Deque, Dict, List, Optional


class AnomalySeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    """A detected anomaly in a metric."""
    metric_name: str
    value: float
    expected_range: tuple  # (low, high)
    z_score: float
    severity: AnomalySeverity
    timestamp: float = field(default_factory=time.time)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric_name,
            "value": self.value,
            "expected_low": self.expected_range[0],
            "expected_high": self.expected_range[1],
            "z_score": round(self.z_score, 2),
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "message": self.message,
        }


@dataclass
class _MetricWindow:
    """Sliding window statistics for anomaly detection."""
    values: Deque[float] = field(default_factory=lambda: deque(maxlen=500))
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=500))
    _sum: float = 0.0
    _sum_sq: float = 0.0
    _count: int = 0


class AnomalyDetector:
    """Detects anomalies in metric values using statistical analysis.

    Uses z-score analysis to automatically learn normal ranges from
    observed data and flag values that deviate significantly.
    """

    def __init__(
        self,
        window_size: int = 100,
        z_threshold_medium: float = 2.0,
        z_threshold_high: float = 3.0,
        z_threshold_critical: float = 4.0,
        min_samples: int = 10,
    ) -> None:
        self.window_size = window_size
        self.z_threshold_medium = z_threshold_medium
        self.z_threshold_high = z_threshold_high
        self.z_threshold_critical = z_threshold_critical
        self.min_samples = min_samples
        self._windows: Dict[str, _MetricWindow] = {}
        self._anomalies: Deque[Anomaly] = deque(maxlen=1000)
        self._handlers: List[Callable[[Anomaly], None]] = []
        self._lock = Lock()

    def add_handler(self, handler: Callable[[Anomaly], None]) -> None:
        """Register an anomaly handler (called when anomaly is detected)."""
        self._handlers.append(handler)

    def record(self, name: str, value: float) -> List[Anomaly]:
        """Record a metric value. Returns list of anomalies if detected."""
        now = time.time()
        detected: List[Anomaly] = []

        with self._lock:
            if name not in self._windows:
                self._windows[name] = _MetricWindow(
                    values=deque(maxlen=self.window_size),
                    timestamps=deque(maxlen=self.window_size),
                )
            window = self._windows[name]

            if window._count >= self.min_samples:
                mean = window._sum / window._count
                variance = (window._sum_sq / window._count) - (mean * mean)
                stddev = math.sqrt(max(0, variance))

                if stddev > 0:
                    z_score = abs(value - mean) / stddev
                    severity = self._classify_severity(z_score)

                    if severity is not None:
                        expected_low = round(mean - stddev * self.z_threshold_medium, 2)
                        expected_high = round(mean + stddev * self.z_threshold_medium, 2)
                        msg = "{}={} deviates {:.1f} stddevs from mean {:.2f}".format(
                            name, value, z_score, mean
                        )
                        anomaly = Anomaly(
                            metric_name=name,
                            value=value,
                            expected_range=(expected_low, expected_high),
                            z_score=z_score,
                            severity=severity,
                            timestamp=now,
                            message=msg,
                        )
                        detected.append(anomaly)
                        self._anomalies.append(anomaly)

            if len(window.values) == window.values.maxlen:
                old = window.values[0]
                window._sum -= old
                window._sum_sq -= old * old
                window._count -= 1

            window.values.append(value)
            window.timestamps.append(now)
            window._sum += value
            window._sum_sq += value * value
            window._count += 1

        for anomaly in detected:
            for handler in self._handlers:
                try:
                    handler(anomaly)
                except Exception:
                    pass

        return detected

    def _classify_severity(self, z_score: float) -> Optional[AnomalySeverity]:
        """Classify anomaly severity based on z-score."""
        if z_score >= self.z_threshold_critical:
            return AnomalySeverity.CRITICAL
        elif z_score >= self.z_threshold_high:
            return AnomalySeverity.HIGH
        elif z_score >= self.z_threshold_medium:
            return AnomalySeverity.MEDIUM
        return None

    def get_anomalies(
        self,
        name: Optional[str] = None,
        since: Optional[float] = None,
        min_severity: Optional[AnomalySeverity] = None,
    ) -> List[Anomaly]:
        """Get detected anomalies, optionally filtered."""
        severity_order = {
            AnomalySeverity.LOW: 0,
            AnomalySeverity.MEDIUM: 1,
            AnomalySeverity.HIGH: 2,
            AnomalySeverity.CRITICAL: 3,
        }
        min_order = severity_order.get(min_severity, 0) if min_severity else 0

        with self._lock:
            results = []
            for a in self._anomalies:
                if name and a.metric_name != name:
                    continue
                if since and a.timestamp < since:
                    continue
                if severity_order.get(a.severity, 0) < min_order:
                    continue
                results.append(a)
            return results

    def get_stats(self, name: str) -> Dict[str, Any]:
        """Get current statistical profile for a metric."""
        with self._lock:
            window = self._windows.get(name)
            if window is None or window._count == 0:
                return {"name": name, "tracked": False}

            mean = window._sum / window._count
            variance = (window._sum_sq / window._count) - (mean * mean)
            stddev = math.sqrt(max(0, variance))

            return {
                "name": name,
                "tracked": True,
                "count": window._count,
                "mean": round(mean, 4),
                "stddev": round(stddev, 4),
                "expected_low": round(mean - stddev * self.z_threshold_medium, 4),
                "expected_high": round(mean + stddev * self.z_threshold_medium, 4),
            }

    def reset(self, name: Optional[str] = None) -> None:
        with self._lock:
            if name is None:
                self._windows.clear()
                self._anomalies.clear()
            else:
                self._windows.pop(name, None)
