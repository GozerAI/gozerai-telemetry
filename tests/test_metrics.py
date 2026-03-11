"""Tests for metrics collection."""

from gozerai_telemetry.metrics import Counter, Gauge, Histogram, MetricsCollector, get_collector


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


class TestGetCollector:
    def test_returns_same_instance(self):
        c1 = get_collector("svc_a")
        c2 = get_collector("svc_a")
        assert c1 is c2

    def test_different_services(self):
        c1 = get_collector("svc_x")
        c2 = get_collector("svc_y")
        assert c1 is not c2
