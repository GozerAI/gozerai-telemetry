"""Efficient histogram with pre-allocated bucket arrays and binary search.

Standard Histogram scans all buckets linearly on each observe(). This
implementation uses bisect for O(log n) bucket lookup and pre-allocates
arrays to avoid dynamic allocation.
"""

from __future__ import annotations

import bisect
import time
from array import array
from threading import Lock
from typing import Dict, List, Optional, Tuple

from gozerai_telemetry.metrics import _labels_key, _labels_prometheus


class EfficientHistogram:
    """Histogram with pre-allocated arrays and binary-search bucketing.

    Differences from the base Histogram:
    - Uses array('q') for bucket counts (fixed-size integer arrays)
    - Uses bisect for O(log n) bucket lookup instead of O(n) linear scan
    - Pre-allocates per-label arrays on first observation
    - Supports min/max/avg tracking per label set

    Usage:
        h = EfficientHistogram("request_duration", buckets=(0.01, 0.05, 0.1, 0.5, 1.0))
        h.observe(0.042, method="GET")
    """

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self,
        name: str,
        description: str = "",
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> None:
        self.name = name
        self.description = description
        self._buckets = tuple(sorted(buckets or self.DEFAULT_BUCKETS))
        self._bucket_list: List[float] = list(self._buckets)
        self._num_buckets = len(self._buckets)
        self._counts: Dict[Tuple[Tuple[str, str], ...], array] = {}
        self._sums: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._totals: Dict[Tuple[Tuple[str, str], ...], int] = {}
        self._mins: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._maxs: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._lock = Lock()

    def _ensure_key(self, key: Tuple[Tuple[str, str], ...]) -> None:
        """Pre-allocate arrays for a new label set (must be called under lock)."""
        if key not in self._counts:
            self._counts[key] = array("q", [0] * self._num_buckets)
            self._sums[key] = 0.0
            self._totals[key] = 0
            self._mins[key] = float("inf")
            self._maxs[key] = float("-inf")

    def observe(self, value: float, **labels: str) -> None:
        """Record a value. Uses bisect for efficient bucket assignment."""
        key = _labels_key(**labels)
        idx = bisect.bisect_left(self._bucket_list, value)
        with self._lock:
            self._ensure_key(key)
            for i in range(idx, self._num_buckets):
                self._counts[key][i] += 1
            self._sums[key] += value
            self._totals[key] += 1
            if value < self._mins[key]:
                self._mins[key] = value
            if value > self._maxs[key]:
                self._maxs[key] = value

    def time(self, **labels: str):
        """Context manager to time a block and observe the duration."""
        return _EfficientTimer(self, labels)

    def get_stats(self, **labels: str) -> Dict:
        """Get statistics for a given label set."""
        key = _labels_key(**labels)
        with self._lock:
            if key not in self._totals or self._totals[key] == 0:
                return {"count": 0, "sum": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
            return {
                "count": self._totals[key],
                "sum": self._sums[key],
                "min": self._mins[key],
                "max": self._maxs[key],
                "avg": self._sums[key] / self._totals[key],
            }

    def to_prometheus(self) -> str:
        """Export in Prometheus exposition format."""
        lines = []
        if self.description:
            lines.append(f"# HELP {self.name} {self.description}")
        lines.append(f"# TYPE {self.name} histogram")
        with self._lock:
            for key in sorted(self._counts.keys()):
                lp = _labels_prometheus(key)
                for i, bound in enumerate(self._buckets):
                    le_labels = dict(key) | {"le": str(bound)}
                    le_lp = _labels_prometheus(_labels_key(**le_labels))
                    lines.append(f"{self.name}_bucket{le_lp} {self._counts[key][i]}")
                inf_labels = dict(key) | {"le": "+Inf"}
                inf_lp = _labels_prometheus(_labels_key(**inf_labels))
                lines.append(f"{self.name}_bucket{inf_lp} {self._totals[key]}")
                lines.append(f"{self.name}_sum{lp} {self._sums[key]}")
                lines.append(f"{self.name}_count{lp} {self._totals[key]}")
        return "\n".join(lines)


class _EfficientTimer:
    def __init__(self, histogram: EfficientHistogram, labels: dict) -> None:
        self._histogram = histogram
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> _EfficientTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *args) -> None:
        duration = time.monotonic() - self._start
        self._histogram.observe(duration, **self._labels)
