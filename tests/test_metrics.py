"""Tests for metrics collection."""

import logging
import threading

from gozerai_telemetry.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    get_collector,
    reset_collectors,
    _collectors,
)


class TestCounter:
    def test_inc_default(self):
        c = Counter("test_total")
        c.inc()
        assert c.get() == 1.0

    def test_inc_amount(self):
        c = Counter("test_total")
        c.inc(5)
        assert c.get() == 5.0

    def test_inc_with_labels(self):
        c = Counter("http_total")
        c.inc(method="GET", status="200")
        c.inc(method="POST", status="201")
        assert c.get(method="GET", status="200") == 1.0
        assert c.get(method="POST", status="201") == 1.0
        assert c.get(method="DELETE", status="404") == 0.0

    def test_cumulative(self):
        c = Counter("test_total")
        c.inc(3)
        c.inc(7)
        assert c.get() == 10.0

    def test_prometheus_format(self):
        c = Counter("requests_total", "Total requests")
        c.inc(method="GET")
        text = c.to_prometheus()
        assert "# HELP requests_total Total requests" in text
        assert "# TYPE requests_total counter" in text
        assert 'requests_total{method="GET"}' in text

    def test_inc_negative_amount(self):
        c = Counter("test_neg")
        c.inc(5)
        c.inc(-1)
        assert c.get() == 4.0

    def test_inc_zero(self):
        c = Counter("test_zero")
        c.inc(10)
        c.inc(0)
        assert c.get() == 10.0

    def test_get_nonexistent_labels(self):
        c = Counter("test_labels")
        c.inc(method="GET")
        assert c.get(method="POST") == 0.0
        assert c.get(method="GET", status="200") == 0.0

    def test_prometheus_no_description(self):
        c = Counter("nodesc_total")
        c.inc()
        text = c.to_prometheus()
        assert "# HELP" not in text
        assert "# TYPE nodesc_total counter" in text

    def test_prometheus_multiple_label_sets(self):
        c = Counter("multi_total", "Multi")
        c.inc(method="GET", status="200")
        c.inc(method="POST", status="201")
        c.inc(method="DELETE", status="404")
        text = c.to_prometheus()
        assert 'method="GET"' in text
        assert 'method="POST"' in text
        assert 'method="DELETE"' in text
        # All three label combos should appear as separate lines
        lines = [l for l in text.split("\n") if l.startswith("multi_total{")]
        assert len(lines) == 3


class TestGauge:
    def test_set(self):
        g = Gauge("temperature")
        g.set(72.5)
        assert g.get() == 72.5

    def test_inc_dec(self):
        g = Gauge("connections")
        g.inc()
        g.inc()
        g.dec()
        assert g.get() == 1.0

    def test_labels(self):
        g = Gauge("queue_size")
        g.set(10, queue="high")
        g.set(5, queue="low")
        assert g.get(queue="high") == 10
        assert g.get(queue="low") == 5

    def test_prometheus_format(self):
        g = Gauge("temp", "Temperature")
        g.set(72.5)
        text = g.to_prometheus()
        assert "# TYPE temp gauge" in text
        assert "temp 72.5" in text

    def test_concurrent_inc_dec(self):
        g = Gauge("conc_gauge")
        g.set(0)
        barrier = threading.Barrier(10)

        def inc_dec(i):
            barrier.wait()
            if i % 2 == 0:
                g.inc(1)
            else:
                g.dec(1)

        threads = [threading.Thread(target=inc_dec, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 5 incs and 5 decs from 0 => net 0
        assert g.get() == 0.0

    def test_set_negative(self):
        g = Gauge("neg_gauge")
        g.set(-100)
        assert g.get() == -100

    def test_set_zero(self):
        g = Gauge("zero_gauge")
        g.set(100)
        g.set(0)
        assert g.get() == 0.0

    def test_prometheus_no_description(self):
        g = Gauge("nodesc_gauge")
        g.set(1)
        text = g.to_prometheus()
        assert "# HELP" not in text
        assert "# TYPE nodesc_gauge gauge" in text


class TestHistogram:
    def test_observe(self):
        h = Histogram("duration", buckets=(0.1, 0.5, 1.0))
        h.observe(0.05)
        h.observe(0.3)
        h.observe(0.8)
        assert h._totals[()]  == 3
        assert h._sums[()] == 0.05 + 0.3 + 0.8

    def test_time_context_manager(self):
        h = Histogram("op_duration")
        with h.time():
            pass  # should record a very small duration
        assert h._totals[()] == 1
        assert h._sums[()] > 0

    def test_prometheus_format(self):
        h = Histogram("req_duration", "Request duration", buckets=(0.1, 1.0))
        h.observe(0.05)
        text = h.to_prometheus()
        assert "req_duration_bucket" in text
        assert "req_duration_sum" in text
        assert "req_duration_count" in text

    def test_observe_negative_value(self):
        h = Histogram("neg_hist", buckets=(0.1, 0.5, 1.0))
        h.observe(-1.0)
        key = ()
        assert h._totals[key] == 1
        assert h._sums[key] == -1.0
        # -1.0 <= all positive buckets, so it falls in every bucket
        assert all(c == 1 for c in h._counts[key])

    def test_observe_extreme_outlier(self):
        h = Histogram("outlier_hist", buckets=(0.1, 0.5, 1.0))
        h.observe(999999)
        key = ()
        assert h._totals[key] == 1
        assert h._sums[key] == 999999
        # No bucket captures this value
        assert all(c == 0 for c in h._counts[key])

    def test_observe_exact_bucket_boundary(self):
        h = Histogram("boundary_hist", buckets=(0.1, 0.5, 1.0))
        h.observe(0.5)
        key = ()
        assert h._totals[key] == 1
        # 0.5 <= 0.5 is True, so bucket index 1 should be 1
        assert h._counts[key][0] == 0  # 0.5 > 0.1
        assert h._counts[key][1] == 1  # 0.5 <= 0.5
        assert h._counts[key][2] == 1  # 0.5 <= 1.0

    def test_observe_zero(self):
        h = Histogram("zero_hist", buckets=(0.1, 0.5, 1.0))
        h.observe(0.0)
        key = ()
        assert h._totals[key] == 1
        assert h._sums[key] == 0.0
        # 0.0 <= all buckets
        assert all(c == 1 for c in h._counts[key])

    def test_custom_buckets(self):
        h = Histogram("custom_hist", buckets=(1, 5, 10))
        h.observe(3)
        h.observe(7)
        key = ()
        assert h._totals[key] == 2
        assert h._sums[key] == 10
        assert h._counts[key][0] == 0  # 3 > 1, 7 > 1
        assert h._counts[key][1] == 1  # 3 <= 5, 7 > 5
        assert h._counts[key][2] == 2  # 3 <= 10, 7 <= 10

    def test_time_with_labels(self):
        h = Histogram("labeled_time", buckets=(0.1, 1.0, 10.0))
        with h.time(endpoint="/api"):
            pass
        key = (("endpoint", "/api"),)
        assert h._totals[key] == 1
        assert h._sums[key] > 0


class TestMetricsCollector:
    def test_creates_prefixed_metrics(self):
        mc = MetricsCollector(service_name="myapp")
        c = mc.counter("requests", "Total requests")
        assert c.name == "myapp_requests"

    def test_same_name_returns_same_instance(self):
        mc = MetricsCollector(service_name="myapp")
        c1 = mc.counter("requests")
        c2 = mc.counter("requests")
        assert c1 is c2

    def test_gauge_and_histogram(self):
        mc = MetricsCollector(service_name="svc")
        g = mc.gauge("connections")
        h = mc.histogram("latency")
        assert g.name == "svc_connections"
        assert h.name == "svc_latency"

    def test_prometheus_export(self):
        mc = MetricsCollector(service_name="test")
        mc.counter("total").inc()
        mc.gauge("active").set(5)
        text = mc.to_prometheus()
        assert "test_total" in text
        assert "test_active" in text
        assert "test_uptime_seconds" in text

    def test_dict_export(self):
        mc = MetricsCollector(service_name="test")
        mc.counter("total").inc(3)
        d = mc.to_dict()
        assert d["service"] == "test"
        assert "uptime" in d

    def test_uptime_increases(self):
        import time
        mc = MetricsCollector(service_name="test")
        mc._created_at = time.time() - 10
        d = mc.to_dict()
        assert d["uptime"] >= 10

    def test_histogram_with_custom_buckets(self):
        mc = MetricsCollector(service_name="svc")
        h = mc.histogram("latency", buckets=(0.1, 1.0))
        h.observe(0.05)
        assert h._buckets == (0.1, 1.0)
        assert h._totals[()] == 1

    def test_to_dict_with_labeled_counter(self):
        mc = MetricsCollector(service_name="svc")
        c = mc.counter("requests")
        c.inc(method="GET")
        c.inc(method="POST")
        d = mc.to_dict()
        # The labeled counter should be a dict (not 0.0)
        assert isinstance(d["svc_requests"], dict)

    def test_to_dict_empty_collector(self):
        mc = MetricsCollector(service_name="empty")
        d = mc.to_dict()
        assert d["service"] == "empty"
        assert "uptime" in d
        # Only service and uptime keys
        assert len(d) == 2


class TestMetricsCollectorSingleton:
    def setup_method(self):
        # Clean up global registry to avoid cross-test contamination
        _collectors.clear()

    def test_get_collector_isolation(self):
        c1 = get_collector("alpha")
        c2 = get_collector("beta")
        c1.counter("hits").inc()
        # beta should have no metrics
        assert c2.to_dict().get("beta_hits") is None
        assert c1.to_dict()["alpha_hits"] != 0.0

    def test_get_collector_same_metrics_same_instance(self):
        mc = get_collector("singleton_test")
        counter_a = mc.counter("total")
        counter_b = mc.counter("total")
        assert counter_a is counter_b


class TestGetCollector:
    def test_returns_same_instance(self):
        c1 = get_collector("svc_a")
        c2 = get_collector("svc_a")
        assert c1 is c2

    def test_different_services(self):
        c1 = get_collector("svc_x")
        c2 = get_collector("svc_y")
        assert c1 is not c2


# ── Gap 1: reset_collectors() and .reset() for test isolation ──


class TestResetCollectors:
    def setup_method(self):
        _collectors.clear()

    def test_reset_collectors_clears_registry(self):
        get_collector("a")
        get_collector("b")
        assert len(_collectors) == 2
        reset_collectors()
        assert len(_collectors) == 0

    def test_reset_collectors_new_instance_after_reset(self):
        c1 = get_collector("svc")
        reset_collectors()
        c2 = get_collector("svc")
        assert c1 is not c2

    def test_reset_collectors_importable_from_package(self):
        from gozerai_telemetry import reset_collectors as rc
        assert callable(rc)


class TestCounterReset:
    def test_reset_clears_all_values(self):
        c = Counter("reset_test")
        c.inc(5, method="GET")
        c.inc(3, method="POST")
        c.reset()
        assert c.get(method="GET") == 0.0
        assert c.get(method="POST") == 0.0
        assert len(c._values) == 0

    def test_reset_allows_reuse(self):
        c = Counter("reuse_test")
        c.inc(10)
        c.reset()
        c.inc(1)
        assert c.get() == 1.0

    def test_reset_empty_counter(self):
        c = Counter("empty_reset")
        c.reset()
        assert c.get() == 0.0


class TestGaugeReset:
    def test_reset_clears_all_values(self):
        g = Gauge("reset_gauge")
        g.set(42, env="prod")
        g.set(10, env="dev")
        g.reset()
        assert g.get(env="prod") == 0.0
        assert g.get(env="dev") == 0.0
        assert len(g._values) == 0

    def test_reset_allows_reuse(self):
        g = Gauge("reuse_gauge")
        g.set(100)
        g.reset()
        g.set(5)
        assert g.get() == 5.0

    def test_reset_empty_gauge(self):
        g = Gauge("empty_gauge_reset")
        g.reset()
        assert g.get() == 0.0


class TestHistogramReset:
    def test_reset_clears_all_values(self):
        h = Histogram("reset_hist", buckets=(0.1, 1.0))
        h.observe(0.05)
        h.observe(0.5, endpoint="/api")
        h.reset()
        assert len(h._counts) == 0
        assert len(h._sums) == 0
        assert len(h._totals) == 0
        assert len(h._observations) == 0

    def test_reset_allows_reuse(self):
        h = Histogram("reuse_hist", buckets=(1.0,))
        h.observe(0.5)
        h.reset()
        h.observe(0.8)
        assert h._totals[()] == 1
        assert h._sums[()] == 0.8

    def test_reset_empty_histogram(self):
        h = Histogram("empty_hist_reset", buckets=(1.0,))
        h.reset()
        assert len(h._counts) == 0


class TestMetricsCollectorReset:
    def test_reset_clears_all_metric_values(self):
        mc = MetricsCollector(service_name="reset_svc")
        mc.counter("hits").inc(10, method="GET")
        mc.gauge("active").set(5)
        mc.histogram("latency", buckets=(1.0,)).observe(0.5)
        mc.reset()
        assert mc.counter("hits").get(method="GET") == 0.0
        assert mc.gauge("active").get() == 0.0
        assert len(mc.histogram("latency")._totals) == 0

    def test_reset_preserves_metric_registrations(self):
        mc = MetricsCollector(service_name="preserve_svc")
        c = mc.counter("hits")
        g = mc.gauge("active")
        h = mc.histogram("latency")
        mc.reset()
        # Same instances should still be returned
        assert mc.counter("hits") is c
        assert mc.gauge("active") is g
        assert mc.histogram("latency") is h

    def test_reset_then_increment(self):
        mc = MetricsCollector(service_name="incr_svc")
        mc.counter("total").inc(100)
        mc.reset()
        mc.counter("total").inc(1)
        assert mc.counter("total").get() == 1.0


# ── Gap 2: Cardinality limits ──


class TestCounterCardinality:
    def test_default_max_cardinality(self):
        assert Counter.DEFAULT_MAX_CARDINALITY == 1000
        c = Counter("card_test")
        assert c.max_cardinality == 1000

    def test_custom_max_cardinality(self):
        c = Counter("card_custom", max_cardinality=5)
        assert c.max_cardinality == 5

    def test_cardinality_limit_drops_new_label_sets(self):
        c = Counter("card_limit", max_cardinality=3)
        c.inc(user="a")
        c.inc(user="b")
        c.inc(user="c")
        # At limit: new label set should be dropped
        c.inc(user="d")
        assert c.get(user="d") == 0.0
        assert len(c._values) == 3

    def test_cardinality_existing_labels_still_work(self):
        c = Counter("card_existing", max_cardinality=2)
        c.inc(user="a")
        c.inc(user="b")
        # Existing label set should still accept increments
        c.inc(5, user="a")
        assert c.get(user="a") == 6.0

    def test_cardinality_limit_logs_warning(self, caplog):
        c = Counter("card_warn", max_cardinality=1)
        c.inc(user="a")
        with caplog.at_level(logging.WARNING, logger="gozerai_telemetry.metrics"):
            c.inc(user="b")
        assert "cardinality limit" in caplog.text
        assert "card_warn" in caplog.text

    def test_cardinality_zero_blocks_all(self):
        c = Counter("card_zero", max_cardinality=0)
        c.inc(user="a")
        assert c.get(user="a") == 0.0
        assert len(c._values) == 0

    def test_cardinality_no_labels_counts_as_one(self):
        c = Counter("card_nolabel", max_cardinality=1)
        c.inc()  # () key
        c.inc(user="a")  # Should be dropped
        assert c.get() == 1.0
        assert c.get(user="a") == 0.0


class TestGaugeCardinality:
    def test_default_max_cardinality(self):
        assert Gauge.DEFAULT_MAX_CARDINALITY == 1000
        g = Gauge("gcard_test")
        assert g.max_cardinality == 1000

    def test_custom_max_cardinality(self):
        g = Gauge("gcard_custom", max_cardinality=5)
        assert g.max_cardinality == 5

    def test_set_cardinality_limit(self):
        g = Gauge("gcard_set", max_cardinality=2)
        g.set(1.0, env="prod")
        g.set(2.0, env="dev")
        g.set(3.0, env="staging")  # Should be dropped
        assert g.get(env="staging") == 0.0
        assert len(g._values) == 2

    def test_inc_cardinality_limit(self):
        g = Gauge("gcard_inc", max_cardinality=2)
        g.inc(1.0, env="prod")
        g.inc(1.0, env="dev")
        g.inc(1.0, env="staging")  # Should be dropped
        assert g.get(env="staging") == 0.0

    def test_dec_cardinality_limit(self):
        g = Gauge("gcard_dec", max_cardinality=1)
        g.set(10, env="prod")
        g.dec(1, env="new")  # Should be dropped (dec calls inc)
        assert g.get(env="new") == 0.0

    def test_existing_labels_still_work(self):
        g = Gauge("gcard_exist", max_cardinality=1)
        g.set(10, env="prod")
        g.set(20, env="prod")  # Same label, should work
        assert g.get(env="prod") == 20

    def test_set_cardinality_logs_warning(self, caplog):
        g = Gauge("gcard_warn", max_cardinality=1)
        g.set(1.0, env="prod")
        with caplog.at_level(logging.WARNING, logger="gozerai_telemetry.metrics"):
            g.set(2.0, env="dev")
        assert "cardinality limit" in caplog.text
        assert "gcard_warn" in caplog.text


class TestHistogramCardinality:
    def test_default_max_cardinality(self):
        assert Histogram.DEFAULT_MAX_CARDINALITY == 1000
        h = Histogram("hcard_test")
        assert h.max_cardinality == 1000

    def test_custom_max_cardinality(self):
        h = Histogram("hcard_custom", max_cardinality=5)
        assert h.max_cardinality == 5

    def test_observe_cardinality_limit(self):
        h = Histogram("hcard_obs", buckets=(1.0,), max_cardinality=2)
        h.observe(0.5, endpoint="/a")
        h.observe(0.5, endpoint="/b")
        h.observe(0.5, endpoint="/c")  # Should be dropped
        assert len(h._counts) == 2
        assert () not in h._totals or h._totals.get((("endpoint", "/c"),)) is None

    def test_existing_labels_still_work(self):
        h = Histogram("hcard_exist", buckets=(1.0,), max_cardinality=1)
        h.observe(0.5, endpoint="/a")
        h.observe(0.8, endpoint="/a")  # Same label, should work
        key = (("endpoint", "/a"),)
        assert h._totals[key] == 2

    def test_observe_cardinality_logs_warning(self, caplog):
        h = Histogram("hcard_warn", buckets=(1.0,), max_cardinality=1)
        h.observe(0.5, endpoint="/a")
        with caplog.at_level(logging.WARNING, logger="gozerai_telemetry.metrics"):
            h.observe(0.5, endpoint="/b")
        assert "cardinality limit" in caplog.text
        assert "hcard_warn" in caplog.text

    def test_cardinality_after_reset(self):
        h = Histogram("hcard_reset", buckets=(1.0,), max_cardinality=2)
        h.observe(0.5, endpoint="/a")
        h.observe(0.5, endpoint="/b")
        h.observe(0.5, endpoint="/c")  # Dropped
        assert len(h._counts) == 2
        h.reset()
        # After reset, should accept new labels again
        h.observe(0.5, endpoint="/c")
        h.observe(0.5, endpoint="/d")
        assert len(h._counts) == 2

    def test_counter_cardinality_after_reset(self):
        c = Counter("ccard_reset", max_cardinality=2)
        c.inc(user="a")
        c.inc(user="b")
        c.inc(user="c")  # Dropped
        assert len(c._values) == 2
        c.reset()
        c.inc(user="c")
        c.inc(user="d")
        assert len(c._values) == 2
        assert c.get(user="c") == 1.0

    def test_gauge_cardinality_after_reset(self):
        g = Gauge("gcard_reset", max_cardinality=2)
        g.set(1, env="a")
        g.set(1, env="b")
        g.set(1, env="c")  # Dropped
        assert len(g._values) == 2
        g.reset()
        g.set(1, env="c")
        assert g.get(env="c") == 1.0
