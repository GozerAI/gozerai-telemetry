"""Metric aggregation batching for high-throughput scenarios.

BatchCounter accumulates increments in a thread-local buffer and flushes
them to the underlying Counter in batches, reducing lock contention.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from gozerai_telemetry.metrics import Counter, Gauge, Histogram, MetricsCollector, _labels_key


class BatchCounter:
    """Counter that batches increments to reduce lock contention.

    Increments are accumulated in a buffer. When the buffer reaches
    ``batch_size`` or ``flush_interval`` seconds have elapsed since the
    last flush, the batch is flushed to the underlying Counter.

    Usage:
        bc = BatchCounter("requests_total", batch_size=100)
        bc.inc(method="GET")  # buffered
        bc.flush()            # force flush
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ) -> None:
        self._counter = Counter(name, description)
        self.name = name
        self.description = description
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._buffer: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._buffer_count = 0
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._total_flushes = 0

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """Increment the counter. Batched -- may not be immediately visible."""
        key = _labels_key(**labels)
        flush_needed = False
        with self._lock:
            self._buffer[key] = self._buffer.get(key, 0.0) + amount
            self._buffer_count += 1
            now = time.monotonic()
            if (
                self._buffer_count >= self.batch_size
                or (now - self._last_flush) >= self.flush_interval
            ):
                flush_needed = True

        if flush_needed:
            self.flush()

    def flush(self) -> int:
        """Flush buffered increments to the underlying counter.

        Returns the number of label-key entries flushed.
        """
        with self._lock:
            to_flush = self._buffer
            self._buffer = {}
            count = self._buffer_count
            self._buffer_count = 0
            self._last_flush = time.monotonic()
            self._total_flushes += 1

        for key, amount in to_flush.items():
            labels = dict(key)
            self._counter.inc(amount, **labels)

        return len(to_flush)

    def get(self, **labels: str) -> float:
        """Get committed value (does not include unflushed buffer)."""
        return self._counter.get(**labels)

    def get_including_buffer(self, **labels: str) -> float:
        """Get value including any unflushed buffer."""
        key = _labels_key(**labels)
        with self._lock:
            buffered = self._buffer.get(key, 0.0)
        return self._counter.get(**labels) + buffered

    def to_prometheus(self) -> str:
        """Export in Prometheus format. Flushes first."""
        self.flush()
        return self._counter.to_prometheus()

    @property
    def pending_count(self) -> int:
        """Number of increments waiting in the buffer."""
        with self._lock:
            return self._buffer_count

    @property
    def total_flushes(self) -> int:
        return self._total_flushes


class BatchedMetricsCollector:
    """MetricsCollector that uses BatchCounters for high-throughput.

    Drop-in replacement for MetricsCollector where counters are batched.
    Gauges and histograms are passed through unchanged.
    """

    def __init__(
        self,
        service_name: str,
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ) -> None:
        self.service_name = service_name
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._counters: Dict[str, BatchCounter] = {}
        self._inner = MetricsCollector(service_name=service_name)

    def counter(self, name: str, description: str = "") -> BatchCounter:
        prefixed = f"{self.service_name}_{name}"
        if prefixed not in self._counters:
            self._counters[prefixed] = BatchCounter(
                prefixed, description, self.batch_size, self.flush_interval,
            )
        return self._counters[prefixed]

    def gauge(self, name: str, description: str = "") -> Gauge:
        return self._inner.gauge(name, description)

    def histogram(
        self, name: str, description: str = "", buckets: Optional[Tuple[float, ...]] = None
    ) -> Histogram:
        return self._inner.histogram(name, description, buckets)

    def flush_all(self) -> int:
        """Flush all batched counters. Returns total entries flushed."""
        total = 0
        for bc in self._counters.values():
            total += bc.flush()
        return total

    def to_prometheus(self) -> str:
        """Export all metrics. Flushes counters first."""
        sections = []
        for bc in self._counters.values():
            sections.append(bc.to_prometheus())
        sections.append(self._inner.to_prometheus())
        return "\n".join(sections)
