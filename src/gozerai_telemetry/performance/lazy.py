"""Lazy metric initialization -- metrics are only created when first accessed.

Useful for services that register many metrics upfront but only use a
subset per request. Avoids allocating memory for unused metrics.
"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional, Tuple

from gozerai_telemetry.metrics import Counter, Gauge, Histogram


class _LazyDescriptor:
    """Base for lazy metric wrappers. Stores name/description; defers creation."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._initialized = False
        self._lock = Lock()


class LazyCounter(_LazyDescriptor):
    """Counter that defers internal allocation until first inc()/get()."""

    def __init__(self, name: str, description: str = "") -> None:
        super().__init__(name, description)
        self._counter: Optional[Counter] = None

    def _ensure(self) -> Counter:
        if self._counter is None:
            with self._lock:
                if self._counter is None:
                    self._counter = Counter(self.name, self.description)
                    self._initialized = True
        return self._counter

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self._ensure().inc(amount, **labels)

    def get(self, **labels: str) -> float:
        if not self._initialized:
            return 0.0
        return self._ensure().get(**labels)

    def to_prometheus(self) -> str:
        if not self._initialized:
            return ""
        return self._ensure().to_prometheus()


class LazyGauge(_LazyDescriptor):
    """Gauge that defers internal allocation until first set()/inc()/get()."""

    def __init__(self, name: str, description: str = "") -> None:
        super().__init__(name, description)
        self._gauge: Optional[Gauge] = None

    def _ensure(self) -> Gauge:
        if self._gauge is None:
            with self._lock:
                if self._gauge is None:
                    self._gauge = Gauge(self.name, self.description)
                    self._initialized = True
        return self._gauge

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def set(self, value: float, **labels: str) -> None:
        self._ensure().set(value, **labels)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self._ensure().inc(amount, **labels)

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self._ensure().dec(amount, **labels)

    def get(self, **labels: str) -> float:
        if not self._initialized:
            return 0.0
        return self._ensure().get(**labels)

    def to_prometheus(self) -> str:
        if not self._initialized:
            return ""
        return self._ensure().to_prometheus()


class LazyHistogram(_LazyDescriptor):
    """Histogram that defers internal allocation until first observe()."""

    def __init__(
        self, name: str, description: str = "", buckets: Optional[Tuple[float, ...]] = None
    ) -> None:
        super().__init__(name, description)
        self._buckets = buckets
        self._histogram: Optional[Histogram] = None

    def _ensure(self) -> Histogram:
        if self._histogram is None:
            with self._lock:
                if self._histogram is None:
                    self._histogram = Histogram(self.name, self.description, self._buckets)
                    self._initialized = True
        return self._histogram

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def observe(self, value: float, **labels: str) -> None:
        self._ensure().observe(value, **labels)

    def time(self, **labels: str):
        return self._ensure().time(**labels)

    def to_prometheus(self) -> str:
        if not self._initialized:
            return ""
        return self._ensure().to_prometheus()


class LazyMetricsCollector:
    """MetricsCollector that uses lazy metrics -- only allocates on first use.

    Tracks which metrics have been accessed vs. only registered.
    """

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self._counters: Dict[str, LazyCounter] = {}
        self._gauges: Dict[str, LazyGauge] = {}
        self._histograms: Dict[str, LazyHistogram] = {}

    def counter(self, name: str, description: str = "") -> LazyCounter:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._counters:
            self._counters[prefixed] = LazyCounter(prefixed, description)
        return self._counters[prefixed]

    def gauge(self, name: str, description: str = "") -> LazyGauge:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._gauges:
            self._gauges[prefixed] = LazyGauge(prefixed, description)
        return self._gauges[prefixed]

    def histogram(
        self, name: str, description: str = "", buckets: Optional[Tuple[float, ...]] = None
    ) -> LazyHistogram:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._histograms:
            self._histograms[prefixed] = LazyHistogram(prefixed, description, buckets)
        return self._histograms[prefixed]

    @property
    def registered_count(self) -> int:
        """Total number of registered (not necessarily initialized) metrics."""
        return len(self._counters) + len(self._gauges) + len(self._histograms)

    @property
    def initialized_count(self) -> int:
        """Number of metrics that have actually been used."""
        count = 0
        for c in self._counters.values():
            if c.is_initialized:
                count += 1
        for g in self._gauges.values():
            if g.is_initialized:
                count += 1
        for h in self._histograms.values():
            if h.is_initialized:
                count += 1
        return count

    def to_prometheus(self) -> str:
        """Export only initialized metrics in Prometheus format."""
        sections = []
        for c in self._counters.values():
            text = c.to_prometheus()
            if text:
                sections.append(text)
        for g in self._gauges.values():
            text = g.to_prometheus()
            if text:
                sections.append(text)
        for h in self._histograms.values():
            text = h.to_prometheus()
            if text:
                sections.append(text)
        return "\n".join(sections)
