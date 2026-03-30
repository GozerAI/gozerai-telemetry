"""Performance optimizations for high-throughput telemetry.

Provides batched counters, lazy metrics, span pooling, efficient histograms,
and buffered metric export — all with zero external dependencies.
"""

from gozerai_telemetry.performance.batching import BatchCounter, BatchedMetricsCollector
from gozerai_telemetry.performance.lazy import LazyCounter, LazyGauge, LazyHistogram, LazyMetricsCollector
from gozerai_telemetry.performance.span_pool import SpanPool, PooledSpan
from gozerai_telemetry.performance.efficient_histogram import EfficientHistogram
from gozerai_telemetry.performance.export_buffer import ExportBuffer, BufferedExporter

__all__ = [
    "BatchCounter",
    "BatchedMetricsCollector",
    "LazyCounter",
    "LazyGauge",
    "LazyHistogram",
    "LazyMetricsCollector",
    "SpanPool",
    "PooledSpan",
    "EfficientHistogram",
    "ExportBuffer",
    "BufferedExporter",
]
