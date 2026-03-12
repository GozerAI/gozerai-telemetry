"""Prometheus-compatible metrics collection.

Prometheus-compatible metrics collection.
Zero dependencies — pure Python with thread-safe operations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional, Tuple


def _labels_key(**kwargs: str) -> Tuple[Tuple[str, str], ...]:
    return tuple(sorted(kwargs.items()))


def _labels_prometheus(labels: Tuple[Tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in labels]
    return "{" + ",".join(parts) + "}"


class Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._lock = Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(**labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def get(self, **labels: str) -> float:
        return self._values.get(_labels_key(**labels), 0.0)

    def to_prometheus(self) -> str:
        lines = []
        if self.description:
            lines.append(f"# HELP {self.name} {self.description}")
        lines.append(f"# TYPE {self.name} counter")
        for key, value in sorted(self._values.items()):
            lines.append(f"{self.name}{_labels_prometheus(key)} {value}")
        return "\n".join(lines)


class Gauge:
    """Value that can go up and down."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._lock = Lock()

    def set(self, value: float, **labels: str) -> None:
        with self._lock:
            self._values[_labels_key(**labels)] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(**labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def get(self, **labels: str) -> float:
        return self._values.get(_labels_key(**labels), 0.0)

    def to_prometheus(self) -> str:
        lines = []
        if self.description:
            lines.append(f"# HELP {self.name} {self.description}")
        lines.append(f"# TYPE {self.name} gauge")
        for key, value in sorted(self._values.items()):
            lines.append(f"{self.name}{_labels_prometheus(key)} {value}")
        return "\n".join(lines)


class Histogram:
    """Tracks value distribution in configurable buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self,
        name: str,
        description: str = "",
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> None:
        self.name = name
        self.description = description
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: Dict[Tuple[Tuple[str, str], ...], List[int]] = {}
        self._sums: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._totals: Dict[Tuple[Tuple[str, str], ...], int] = {}
        self._lock = Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = _labels_key(**labels)
        with self._lock:
            if key not in self._counts:
                self._counts[key] = [0] * len(self._buckets)
                self._sums[key] = 0.0
                self._totals[key] = 0
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._counts[key][i] += 1
            self._sums[key] += value
            self._totals[key] += 1

    def time(self, **labels: str):
        """Context manager to time a block and observe the duration."""
        return _HistogramTimer(self, labels)

    def to_prometheus(self) -> str:
        lines = []
        if self.description:
            lines.append(f"# HELP {self.name} {self.description}")
        lines.append(f"# TYPE {self.name} histogram")
        for key in sorted(self._counts.keys()):
            lp = _labels_prometheus(key)
            cumulative = 0
            for i, bound in enumerate(self._buckets):
                cumulative += self._counts[key][i]
                le_labels = dict(key) | {"le": str(bound)}
                le_lp = _labels_prometheus(_labels_key(**le_labels))
                lines.append(f"{self.name}_bucket{le_lp} {cumulative}")
            inf_labels = dict(key) | {"le": "+Inf"}
            inf_lp = _labels_prometheus(_labels_key(**inf_labels))
            lines.append(f"{self.name}_bucket{inf_lp} {self._totals[key]}")
            lines.append(f"{self.name}_sum{lp} {self._sums[key]}")
            lines.append(f"{self.name}_count{lp} {self._totals[key]}")
        return "\n".join(lines)


class _HistogramTimer:
    def __init__(self, histogram: Histogram, labels: dict) -> None:
        self._histogram = histogram
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> _HistogramTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *args) -> None:
        duration = time.monotonic() - self._start
        self._histogram.observe(duration, **self._labels)


@dataclass
class MetricsCollector:
    """Central collector for a service's metrics."""

    service_name: str
    _counters: Dict[str, Counter] = field(default_factory=dict)
    _gauges: Dict[str, Gauge] = field(default_factory=dict)
    _histograms: Dict[str, Histogram] = field(default_factory=dict)
    _created_at: float = field(default_factory=time.time)

    def counter(self, name: str, description: str = "") -> Counter:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._counters:
            self._counters[prefixed] = Counter(prefixed, description)
        return self._counters[prefixed]

    def gauge(self, name: str, description: str = "") -> Gauge:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._gauges:
            self._gauges[prefixed] = Gauge(prefixed, description)
        return self._gauges[prefixed]

    def histogram(self, name: str, description: str = "", buckets: Optional[Tuple[float, ...]] = None) -> Histogram:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._histograms:
            self._histograms[prefixed] = Histogram(prefixed, description, buckets)
        return self._histograms[prefixed]

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus exposition format."""
        sections = []
        for c in self._counters.values():
            sections.append(c.to_prometheus())
        for g in self._gauges.values():
            sections.append(g.to_prometheus())
        for h in self._histograms.values():
            sections.append(h.to_prometheus())
        # Add uptime gauge
        uptime = time.time() - self._created_at
        sections.append(f"# HELP {self.service_name}_uptime_seconds Service uptime")
        sections.append(f"# TYPE {self.service_name}_uptime_seconds gauge")
        sections.append(f"{self.service_name}_uptime_seconds {uptime:.1f}")
        return "\n".join(sections)

    def to_dict(self) -> Dict[str, any]:
        """Export as JSON-friendly dict."""
        result: Dict[str, any] = {"service": self.service_name, "uptime": time.time() - self._created_at}
        for name, c in self._counters.items():
            result[name] = dict(c._values) if c._values else 0.0
        for name, g in self._gauges.items():
            result[name] = dict(g._values) if g._values else 0.0
        for name, h in self._histograms.items():
            result[name] = {"count": dict(h._totals), "sum": dict(h._sums)}
        return result


# Global registry
_collectors: Dict[str, MetricsCollector] = {}


def get_collector(service_name: str) -> MetricsCollector:
    """Get or create a metrics collector for a service."""
    if service_name not in _collectors:
        _collectors[service_name] = MetricsCollector(service_name=service_name)
    return _collectors[service_name]
