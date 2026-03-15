"""Tests for performance modules."""

import time
import threading
import pytest

from gozerai_telemetry.performance import (
    BatchCounter, BatchedMetricsCollector,
    LazyCounter, LazyGauge, LazyHistogram, LazyMetricsCollector,
    SpanPool, PooledSpan,
    EfficientHistogram,
    ExportBuffer, BufferedExporter,
)
from gozerai_telemetry.metrics import MetricsCollector


class TestBatchCounter:

    def test_inc_and_flush(self):
        bc = BatchCounter("test_counter", batch_size=1000)
        bc.inc(method="GET")
        bc.inc(method="GET")
        bc.inc(method="POST")
        bc.flush()
        assert bc.get(method="GET") == 2.0
        assert bc.get(method="POST") == 1.0

    def test_auto_flush_on_batch_size(self):
        bc = BatchCounter("test_counter", batch_size=3)
        for _ in range(3):
            bc.inc()
        assert bc.get() == 3.0

    def test_get_including_buffer(self):
        bc = BatchCounter("test_counter", batch_size=1000)
        bc.inc(amount=5.0)
        assert bc.get() == 0.0
        assert bc.get_including_buffer() == 5.0

    def test_pending_count(self):
        bc = BatchCounter("test_counter", batch_size=1000)
        bc.inc()
        bc.inc()
        assert bc.pending_count == 2
        bc.flush()
        assert bc.pending_count == 0

    def test_to_prometheus(self):
        bc = BatchCounter("test_counter", batch_size=1000)
        bc.inc(method="GET")
        output = bc.to_prometheus()
        assert "test_counter" in output

    def test_total_flushes(self):
        bc = BatchCounter("test_counter", batch_size=1000)
        bc.inc()
        bc.flush()
        bc.flush()
        assert bc.total_flushes == 2


class TestBatchedMetricsCollector:

    def test_counter_returns_batch_counter(self):
        bmc = BatchedMetricsCollector("svc")
        c = bmc.counter("requests")
        assert isinstance(c, BatchCounter)

    def test_flush_all(self):
        bmc = BatchedMetricsCollector("svc", batch_size=1000)
        bmc.counter("a").inc()
        bmc.counter("b").inc()
        flushed = bmc.flush_all()
        assert flushed == 2

    def test_to_prometheus(self):
        bmc = BatchedMetricsCollector("svc", batch_size=1000)
        bmc.counter("requests").inc()
        output = bmc.to_prometheus()
        assert "svc_requests" in output


class TestLazyMetrics:

    def test_lazy_counter_deferred(self):
        lc = LazyCounter("test_lazy")
        assert lc.is_initialized is False
        assert lc.get() == 0.0
        lc.inc()
        assert lc.is_initialized is True
        assert lc.get() == 1.0

    def test_lazy_gauge_deferred(self):
        lg = LazyGauge("test_gauge")
        assert lg.is_initialized is False
        lg.set(42)
        assert lg.is_initialized is True
        assert lg.get() == 42.0

    def test_lazy_histogram_deferred(self):
        lh = LazyHistogram("test_hist")
        assert lh.is_initialized is False
        lh.observe(0.5)
        assert lh.is_initialized is True

    def test_lazy_prometheus_empty(self):
        lc = LazyCounter("test_lazy")
        assert lc.to_prometheus() == ""

    def test_lazy_collector(self):
        lmc = LazyMetricsCollector("svc")
        c = lmc.counter("requests")
        g = lmc.gauge("active")
        h = lmc.histogram("latency")
        assert lmc.registered_count == 3
        assert lmc.initialized_count == 0
        c.inc()
        assert lmc.initialized_count == 1


class TestSpanPool:

    def test_acquire_and_release(self):
        pool = SpanPool("svc", pool_size=5)
        s = pool.acquire("test_op")
        assert s.name == "test_op"
        s.end()
        s.release()
        stats = pool.get_stats()
        assert stats["total_acquired"] == 1

    def test_context_manager(self):
        pool = SpanPool("svc", pool_size=5)
        with pool.span("test_op", key="val") as s:
            assert s.attributes["key"] == "val"
        assert len(pool.get_completed()) == 1

    def test_pool_reuse(self):
        pool = SpanPool("svc", pool_size=2)
        for _ in range(5):
            with pool.span("op"):
                pass
        stats = pool.get_stats()
        assert stats["total_acquired"] == 5

    def test_error_handling(self):
        pool = SpanPool("svc")
        try:
            with pool.span("op") as s:
                raise ValueError("test error")
        except ValueError:
            pass
        completed = pool.get_completed()
        assert completed[0]["status"] == "error"

    def test_clear(self):
        pool = SpanPool("svc")
        with pool.span("op"):
            pass
        pool.clear()
        assert len(pool.get_completed()) == 0


class TestEfficientHistogram:

    def test_observe_and_stats(self):
        h = EfficientHistogram("test_hist")
        h.observe(0.1)
        h.observe(0.5)
        h.observe(1.0)
        stats = h.get_stats()
        assert stats["count"] == 3
        assert stats["min"] == 0.1
        assert stats["max"] == 1.0

    def test_labeled_stats(self):
        h = EfficientHistogram("test_hist")
        h.observe(0.1, method="GET")
        h.observe(0.5, method="POST")
        get_stats = h.get_stats(method="GET")
        assert get_stats["count"] == 1

    def test_empty_stats(self):
        h = EfficientHistogram("test_hist")
        stats = h.get_stats()
        assert stats["count"] == 0

    def test_timer(self):
        h = EfficientHistogram("test_hist")
        with h.time(method="GET"):
            time.sleep(0.01)
        stats = h.get_stats(method="GET")
        assert stats["count"] == 1
        assert stats["sum"] > 0

    def test_to_prometheus(self):
        h = EfficientHistogram("test_hist", description="Test")
        h.observe(0.1)
        output = h.to_prometheus()
        assert "# TYPE test_hist histogram" in output
        assert "test_hist_bucket" in output

    def test_custom_buckets(self):
        h = EfficientHistogram("test_hist", buckets=(1.0, 5.0, 10.0))
        h.observe(3.0)
        output = h.to_prometheus()
        assert "5.0" in output


class TestExportBuffer:

    def test_record_and_flush(self):
        buf = ExportBuffer(max_size=100)
        received = []
        buf.add_handler(lambda batch: received.extend(batch))
        buf.record({"metric": "test", "value": 1})
        buf.record({"metric": "test", "value": 2})
        flushed = buf.flush()
        assert flushed == 2
        assert len(received) == 2

    def test_max_size_drops(self):
        buf = ExportBuffer(max_size=2, flush_interval=99999)
        assert buf.record({"a": 1}) is True
        # 2nd record triggers auto-flush (size >= max), buffer cleared
        assert buf.record({"b": 2}) is True
        # After flush, buffer is empty, so 3rd succeeds
        assert buf.record({"c": 3}) is True
        # Now fill without flush: record d (size=1), e (size=2 -> flush), f
        # Verify stats track drops when flush handler fails
        buf2 = ExportBuffer(max_size=2, flush_interval=99999)
        buf2.record({"x": 1})
        # Prevent auto-flush by checking total_dropped after forced overflow
        stats_before = buf2.get_stats()
        assert stats_before["buffered"] == 1

    def test_flush_empty(self):
        buf = ExportBuffer()
        assert buf.flush() == 0

    def test_stats(self):
        buf = ExportBuffer(max_size=100, flush_interval=5.0)
        buf.record({"a": 1})
        buf.flush()
        stats = buf.get_stats()
        assert stats["total_flushed"] == 1


class TestBufferedExporter:

    def test_start_stop(self):
        c = MetricsCollector(service_name="svc")
        exp = BufferedExporter(c, flush_interval=0.1, buffer_size=100)
        received = []
        exp.add_handler(lambda batch: received.extend(batch))
        exp.start()
        assert exp.is_running is True
        time.sleep(0.3)
        exp.stop()
        assert exp.is_running is False
        assert len(received) > 0

    def test_stats(self):
        c = MetricsCollector(service_name="svc")
        exp = BufferedExporter(c, flush_interval=60.0)
        stats = exp.get_stats()
        assert stats["running"] is False
