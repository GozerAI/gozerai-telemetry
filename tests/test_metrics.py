"""Tests for metrics collection."""

import threading

from gozerai_telemetry.metrics import Counter, Gauge, Histogram, MetricsCollector, get_collector, _collectors


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
