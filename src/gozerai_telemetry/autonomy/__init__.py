"""Autonomous self-tuning telemetry components.

Provides health threshold adjustment, circuit breaker tuning, retry
optimization, metric interval tuning, anomaly detection, and offline
metric buffering -- all based on observed data patterns. Zero external
dependencies.
"""

from gozerai_telemetry.autonomy.health_tuner import HealthThresholdTuner
from gozerai_telemetry.autonomy.circuit_tuner import CircuitBreakerTuner
from gozerai_telemetry.autonomy.retry_optimizer import RetryOptimizer
from gozerai_telemetry.autonomy.interval_tuner import IntervalTuner
from gozerai_telemetry.autonomy.anomaly_detector import AnomalyDetector, Anomaly
from gozerai_telemetry.autonomy.offline_buffer import (
    OfflineMetricBuffer,
    BufferedEntry,
    BufferEntryType,
    FlushResult,
    FlushStatus,
)

__all__ = [
    "HealthThresholdTuner",
    "CircuitBreakerTuner",
    "RetryOptimizer",
    "IntervalTuner",
    "AnomalyDetector",
    "Anomaly",
    "OfflineMetricBuffer",
    "BufferedEntry",
    "BufferEntryType",
    "FlushResult",
    "FlushStatus",
]
