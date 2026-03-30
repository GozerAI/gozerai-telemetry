"""Self-tuning metric collection intervals.

Adjusts how often metrics are collected based on volatility: high-change
metrics get collected more frequently, stable metrics less frequently.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Dict, Optional


@dataclass
class _MetricTrack:
    """Tracking data for a single metric's volatility."""
    values: Deque[float] = field(default_factory=lambda: deque(maxlen=100))
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=100))
    current_interval: float = 10.0
    last_collection: float = 0.0


class IntervalTuner:
    """Automatically tunes metric collection intervals based on volatility.

    Metrics that change rapidly get shorter collection intervals (more frequent).
    Stable metrics get longer intervals (less frequent, saves resources).

    Usage:
        tuner = IntervalTuner(min_interval=1.0, max_interval=60.0)
        tuner.record_value("cpu_usage", 45.2)
        tuner.record_value("cpu_usage", 78.1)  # big change -> shorter interval
        interval = tuner.get_interval("cpu_usage")
    """

    def __init__(
        self,
        min_interval: float = 1.0,
        max_interval: float = 60.0,
        default_interval: float = 10.0,
        volatility_window: int = 20,
        sensitivity: float = 1.0,
    ) -> None:
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.default_interval = default_interval
        self.volatility_window = volatility_window
        self.sensitivity = sensitivity
        self._tracks: Dict[str, _MetricTrack] = {}
        self._lock = Lock()

    def record_value(self, name: str, value: float) -> None:
        """Record a new metric value for interval tuning."""
        now = time.time()
        with self._lock:
            if name not in self._tracks:
                self._tracks[name] = _MetricTrack(
                    values=deque(maxlen=self.volatility_window * 5),
                    timestamps=deque(maxlen=self.volatility_window * 5),
                    current_interval=self.default_interval,
                )
            track = self._tracks[name]
            track.values.append(value)
            track.timestamps.append(now)
            track.last_collection = now

            # Recompute interval
            self._update_interval(track)

    def _update_interval(self, track: _MetricTrack) -> None:
        """Recompute collection interval based on recent volatility."""
        values = list(track.values)
        if len(values) < 3:
            return

        # Calculate coefficient of variation (CV) of recent changes
        recent = values[-self.volatility_window:]
        if len(recent) < 3:
            return

        deltas = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
        mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
        mean_val = sum(abs(v) for v in recent) / len(recent) if recent else 1.0

        if mean_val == 0:
            volatility = 0.0
        else:
            volatility = mean_delta / max(mean_val, 1e-10)

        # Map volatility to interval: high volatility -> short interval
        # volatility ~0 -> max_interval, volatility ~1+ -> min_interval
        scaled = volatility * self.sensitivity
        # Exponential mapping
        ratio = math.exp(-scaled * 3.0)
        interval_range = self.max_interval - self.min_interval
        track.current_interval = self.min_interval + interval_range * ratio

        # Clamp
        track.current_interval = max(self.min_interval, min(self.max_interval, track.current_interval))

    def get_interval(self, name: str) -> float:
        """Get the current recommended collection interval for a metric."""
        with self._lock:
            track = self._tracks.get(name)
            if track is None:
                return self.default_interval
            return round(track.current_interval, 2)

    def should_collect(self, name: str) -> bool:
        """Check if enough time has passed since last collection."""
        now = time.time()
        with self._lock:
            track = self._tracks.get(name)
            if track is None:
                return True
            return (now - track.last_collection) >= track.current_interval

    def get_all_intervals(self) -> Dict[str, float]:
        """Get current intervals for all tracked metrics."""
        with self._lock:
            return {
                name: round(track.current_interval, 2)
                for name, track in self._tracks.items()
            }

    def get_stats(self, name: str) -> Dict[str, Any]:
        """Get detailed tuning stats for a metric."""
        with self._lock:
            track = self._tracks.get(name)
            if track is None:
                return {"name": name, "tracked": False}
            values = list(track.values)
            return {
                "name": name,
                "tracked": True,
                "current_interval": round(track.current_interval, 2),
                "sample_count": len(values),
                "last_value": values[-1] if values else None,
                "last_collection": track.last_collection,
            }

    def reset(self, name: Optional[str] = None) -> None:
        with self._lock:
            if name is None:
                self._tracks.clear()
            else:
                self._tracks.pop(name, None)
