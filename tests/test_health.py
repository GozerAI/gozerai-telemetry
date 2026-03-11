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
