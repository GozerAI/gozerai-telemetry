"""Tests for health reporting."""

from gozerai_telemetry.health import HealthReporter, HealthStatus


class TestHealthReporter:
    def test_healthy_when_no_checks(self):
        reporter = HealthReporter("test")
        report = reporter.check_all()
        assert report["status"] == "healthy"
        assert report["service"] == "test"
        assert report["checks"] == []

    def test_healthy_check(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        report = reporter.check_all()
        assert report["status"] == "healthy"
        assert len(report["checks"]) == 1
        assert report["checks"][0]["status"] == "healthy"

    def test_unhealthy_check(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"

    def test_exception_is_unhealthy(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        assert "refused" in report["checks"][0]["message"]

    def test_mixed_checks(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        reporter.register_check("redis", lambda: False)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"

    def test_uptime(self):
        import time
        reporter = HealthReporter("test")
        reporter._started_at = time.time() - 60
        report = reporter.check_all()
        assert report["uptime_seconds"] >= 60

    def test_version(self):
        reporter = HealthReporter("test", version="1.2.3")
        report = reporter.check_all()
        assert report["version"] == "1.2.3"

    def test_is_healthy(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        assert reporter.is_healthy() is True

    def test_is_healthy_false(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        assert reporter.is_healthy() is False

    def test_unregister_check(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        reporter.unregister_check("db")
        assert reporter.is_healthy() is True

    def test_duration_tracked(self):
        import time
        reporter = HealthReporter("test")
        reporter.register_check("slow", lambda: (time.sleep(0.01) or True))
        report = reporter.check_all()
        assert report["checks"][0]["duration_ms"] >= 5

    def test_flapping_check(self):
        call_count = 0

        def flapping():
            nonlocal call_count
            call_count += 1
            return call_count % 2 == 1  # True, False, True, False...

        reporter = HealthReporter("test")
        reporter.register_check("flapper", flapping)

        r1 = reporter.check_all()
        assert r1["status"] == "healthy"  # call_count=1, True

        r2 = reporter.check_all()
        assert r2["status"] == "unhealthy"  # call_count=2, False

        r3 = reporter.check_all()
        assert r3["status"] == "healthy"  # call_count=3, True

    def test_multiple_unhealthy(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        reporter.register_check("redis", lambda: False)
        reporter.register_check("cache", lambda: False)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        unhealthy_checks = [c for c in report["checks"] if c["status"] == "unhealthy"]
        assert len(unhealthy_checks) == 2

    def test_degraded_status_from_exception(self):
        """A check that raises with 'degraded' in the message still counts as unhealthy
        because the framework only supports True/False returns."""
        reporter = HealthReporter("test")

        def degraded_check():
            raise RuntimeError("degraded: partial outage")

        reporter.register_check("partial", degraded_check)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        assert "degraded" in report["checks"][0]["message"]

    def test_many_checks_performance(self):
        import time
        reporter = HealthReporter("test")
        for i in range(100):
            reporter.register_check(f"check_{i}", lambda: True)

        start = time.monotonic()
        report = reporter.check_all()
        elapsed = time.monotonic() - start

        assert report["status"] == "healthy"
        assert len(report["checks"]) == 100
        assert elapsed < 1.0  # 100 trivial checks should be very fast

    def test_check_order_preserved(self):
        reporter = HealthReporter("test")
        names = ["alpha", "beta", "gamma", "delta"]
        for name in names:
            reporter.register_check(name, lambda: True)
        report = reporter.check_all()
        check_names = [c["name"] for c in report["checks"]]
        assert check_names == names

    def test_reregister_check(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        reporter.register_check("db", lambda: True)  # Replace
        report = reporter.check_all()
        assert report["status"] == "healthy"
        assert len(report["checks"]) == 1

    def test_unregister_nonexistent(self):
        reporter = HealthReporter("test")
        # Should not raise
        reporter.unregister_check("nonexistent")
        assert reporter.is_healthy() is True
