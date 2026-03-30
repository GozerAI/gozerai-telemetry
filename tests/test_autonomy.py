"""Tests for autonomy modules including offline metric buffering."""

import time
import pytest

from gozerai_telemetry.autonomy import (
    HealthThresholdTuner,
    CircuitBreakerTuner,
    RetryOptimizer,
    IntervalTuner,
    AnomalyDetector,
    Anomaly,
    OfflineMetricBuffer,
    BufferedEntry,
    BufferEntryType,
    FlushResult,
    FlushStatus,
)


# ═══════════════════════════════════════════════════════════════════════
# OfflineMetricBuffer
# ═══════════════════════════════════════════════════════════════════════

class TestOfflineMetricBuffer:

    def test_record_metric(self):
        buf = OfflineMetricBuffer()
        entry = buf.record_metric("http_requests", 1.0, method="GET")
        assert entry.entry_type == BufferEntryType.METRIC
        assert entry.name == "http_requests"
        assert entry.value == 1.0
        assert entry.labels["method"] == "GET"
        assert buf.pending_count() == 1

    def test_record_health(self):
        buf = OfflineMetricBuffer()
        entry = buf.record_health("database", 1.0, latency=0.05)
        assert entry.entry_type == BufferEntryType.HEALTH
        assert entry.metadata["latency"] == 0.05

    def test_record_trace(self):
        buf = OfflineMetricBuffer()
        entry = buf.record_trace("collect_trends", 0.042, source="github")
        assert entry.entry_type == BufferEntryType.TRACE
        assert entry.value == 0.042

    def test_max_size_eviction(self):
        buf = OfflineMetricBuffer(max_size=3)
        for i in range(5):
            buf.record_metric(f"m{i}", float(i))
        assert buf.stats["buffer_size"] == 3
        assert buf.stats["total_dropped"] == 2

    def test_flush_sync_success(self):
        buf = OfflineMetricBuffer()
        buf.record_metric("m1", 1.0)
        buf.record_metric("m2", 2.0)

        received = []
        def export_fn(entries):
            received.extend(entries)
            return True

        result = buf.flush_sync(export_fn)
        assert result.status == FlushStatus.SUCCESS
        assert result.flushed == 2
        assert len(received) == 2
        assert buf.pending_count() == 0

    def test_flush_sync_failure(self):
        buf = OfflineMetricBuffer()
        buf.record_metric("m1", 1.0)

        def export_fn(entries):
            return False

        result = buf.flush_sync(export_fn)
        assert result.status == FlushStatus.FAILED
        assert result.failed == 1
        assert buf.pending_count() == 1  # still pending

    def test_flush_sync_exception(self):
        buf = OfflineMetricBuffer()
        buf.record_metric("m1", 1.0)

        def export_fn(entries):
            raise ConnectionError("backend unavailable")

        result = buf.flush_sync(export_fn)
        assert result.status == FlushStatus.FAILED
        assert "backend unavailable" in result.errors[0]

    def test_flush_empty_buffer(self):
        buf = OfflineMetricBuffer()
        result = buf.flush_sync(lambda entries: True)
        assert result.status == FlushStatus.SUCCESS
        assert result.total == 0

    def test_batch_size_limits_flush(self):
        buf = OfflineMetricBuffer(batch_size=2)
        for i in range(5):
            buf.record_metric(f"m{i}", float(i))

        result = buf.flush_sync(lambda entries: True)
        assert result.flushed == 2
        assert buf.pending_count() == 3

    def test_expire_old(self):
        buf = OfflineMetricBuffer(max_age_seconds=0.0)
        buf.record_metric("m1", 1.0)
        expired = buf.expire_old()
        assert expired == 1
        assert buf.pending_count() == 0
        assert buf.stats["total_expired"] == 1

    def test_set_online(self):
        buf = OfflineMetricBuffer()
        assert buf.is_online is True
        buf.set_online(False)
        assert buf.is_online is False
        buf.set_online(True)
        assert buf.is_online is True

    def test_get_entries_filtered(self):
        buf = OfflineMetricBuffer()
        buf.record_metric("m1", 1.0)
        buf.record_health("db", 1.0)
        buf.record_trace("span", 0.1)

        metrics = buf.get_entries(entry_type=BufferEntryType.METRIC)
        assert len(metrics) == 1
        assert metrics[0].name == "m1"

        health = buf.get_entries(entry_type=BufferEntryType.HEALTH)
        assert len(health) == 1

    def test_get_entries_limit(self):
        buf = OfflineMetricBuffer()
        for i in range(10):
            buf.record_metric(f"m{i}", float(i))
        entries = buf.get_entries(limit=3)
        assert len(entries) == 3

    def test_clear(self):
        buf = OfflineMetricBuffer()
        buf.record_metric("m1", 1.0)
        buf.clear()
        assert buf.pending_count() == 0

    def test_stats(self):
        buf = OfflineMetricBuffer(max_size=1000)
        buf.record_metric("m1", 1.0)
        buf.record_metric("m2", 2.0)
        stats = buf.stats
        assert stats["buffer_size"] == 2
        assert stats["pending"] == 2
        assert stats["total_buffered"] == 2
        assert stats["total_flushed"] == 0
        assert stats["is_online"] is True

    def test_entry_to_dict(self):
        entry = BufferedEntry(
            entry_type=BufferEntryType.METRIC,
            name="test",
            value=42.0,
            labels={"env": "prod"},
        )
        d = entry.to_dict()
        assert d["type"] == "metric"
        assert d["name"] == "test"
        assert d["value"] == 42.0

    def test_flush_result_to_dict(self):
        result = FlushResult(
            status=FlushStatus.PARTIAL, total=10, flushed=7, failed=3,
        )
        d = result.to_dict()
        assert d["status"] == "partial"
        assert d["total"] == 10

    def test_multiple_flush_cycles(self):
        buf = OfflineMetricBuffer(batch_size=2)
        for i in range(6):
            buf.record_metric(f"m{i}", float(i))

        # Flush 3 batches
        total_flushed = 0
        for _ in range(3):
            result = buf.flush_sync(lambda entries: True)
            total_flushed += result.flushed

        assert total_flushed == 6
        assert buf.pending_count() == 0


# ═══════════════════════════════════════════════════════════════════════
# Existing autonomy module smoke tests
# ═══════════════════════════════════════════════════════════════════════

class TestAutonomyImports:
    """Verify all autonomy components are importable and constructable."""

    def test_health_tuner(self):
        tuner = HealthThresholdTuner()
        assert tuner is not None

    def test_circuit_tuner(self):
        tuner = CircuitBreakerTuner()
        assert tuner is not None

    def test_retry_optimizer(self):
        opt = RetryOptimizer()
        assert opt is not None

    def test_interval_tuner(self):
        tuner = IntervalTuner()
        assert tuner is not None

    def test_anomaly_detector(self):
        detector = AnomalyDetector()
        assert detector is not None


class TestAnomalyDetectorBasic:

    def test_record_normal(self):
        detector = AnomalyDetector(min_samples=5)
        for v in [10.0] * 10:
            detector.record("cpu", v)
        # Normal value should not trigger
        anomalies = detector.record("cpu", 10.5)
        assert anomalies == []

    def test_record_anomaly(self):
        detector = AnomalyDetector(min_samples=5, z_threshold_medium=2.0)
        # Use slight variation so stddev > 0
        import random
        random.seed(42)
        for _ in range(20):
            detector.record("cpu", 10.0 + random.gauss(0, 0.5))
        # Huge spike
        anomalies = detector.record("cpu", 1000.0)
        assert len(anomalies) >= 1
        assert anomalies[0].metric_name == "cpu"

    def test_get_stats(self):
        detector = AnomalyDetector()
        for v in [10.0] * 15:
            detector.record("cpu", v)
        stats = detector.get_stats("cpu")
        assert stats["tracked"] is True
        assert stats["count"] == 15

    def test_handler_called(self):
        import random
        random.seed(99)
        detector = AnomalyDetector(min_samples=5)
        alerts = []
        detector.add_handler(lambda a: alerts.append(a))
        for _ in range(20):
            detector.record("cpu", 10.0 + random.gauss(0, 0.5))
        detector.record("cpu", 1000.0)
        assert len(alerts) >= 1

    def test_reset(self):
        detector = AnomalyDetector()
        for v in [10.0] * 15:
            detector.record("cpu", v)
        detector.reset("cpu")
        stats = detector.get_stats("cpu")
        assert stats["tracked"] is False

    def test_reset_all(self):
        detector = AnomalyDetector()
        detector.record("cpu", 10.0)
        detector.record("mem", 50.0)
        detector.reset()
        assert detector.get_stats("cpu")["tracked"] is False
        assert detector.get_stats("mem")["tracked"] is False
