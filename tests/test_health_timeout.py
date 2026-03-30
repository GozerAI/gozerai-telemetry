"""Tests for health check timeout and critical/non-critical features."""

import time
import threading

from gozerai_telemetry.health import HealthReporter, HealthStatus


class TestHealthCheckTimeout:
    def test_check_times_out(self):
        """A hung check should be marked unhealthy after timeout."""
        reporter = HealthReporter("test")

        def hung_check():
            time.sleep(10)
            return True

        reporter.register_check("hung", hung_check, timeout=0.2)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        assert report["checks"][0]["status"] == "unhealthy"
        assert "timed out" in report["checks"][0]["message"].lower()

    def test_timeout_duration_is_bounded(self):
        """check_all should return within roughly the timeout, not hang."""
        reporter = HealthReporter("test")

        def hung_check():
            time.sleep(10)
            return True

        reporter.register_check("hung", hung_check, timeout=0.3)
        start = time.monotonic()
        reporter.check_all()
        elapsed = time.monotonic() - start
        assert elapsed < 2.0  # generous buffer, but not 10s

    def test_fast_check_not_affected_by_timeout(self):
        """A fast check with a timeout should still report healthy."""
        reporter = HealthReporter("test")
        reporter.register_check("fast", lambda: True, timeout=5.0)
        report = reporter.check_all()
        assert report["status"] == "healthy"

    def test_default_timeout_is_5_seconds(self):
        """register_check without timeout should default to 5.0."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        _, timeout, _ = reporter._checks["db"]
        assert timeout == 5.0

    def test_custom_timeout(self):
        """register_check with explicit timeout should store it."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True, timeout=10.0)
        _, timeout, _ = reporter._checks["db"]
        assert timeout == 10.0

    def test_mixed_timeout_and_healthy(self):
        """One timed-out check + one healthy check."""
        reporter = HealthReporter("test")

        def hung():
            time.sleep(10)
            return True

        reporter.register_check("hung", hung, timeout=0.2)
        reporter.register_check("ok", lambda: True, timeout=5.0)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        assert statuses["hung"] == "unhealthy"
        assert statuses["ok"] == "healthy"


class TestHealthCheckCritical:
    def test_critical_failure_is_unhealthy(self):
        """A critical check failure should set overall to unhealthy."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False, critical=True)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        assert report["checks"][0]["status"] == "unhealthy"

    def test_non_critical_failure_is_degraded(self):
        """A non-critical check failure should set overall to degraded."""
        reporter = HealthReporter("test")
        reporter.register_check("cache", lambda: False, critical=False)
        report = reporter.check_all()
        assert report["status"] == "degraded"
        assert report["checks"][0]["status"] == "degraded"

    def test_non_critical_exception_is_degraded(self):
        """A non-critical check that raises should be degraded."""
        reporter = HealthReporter("test")

        def bad_check():
            raise ConnectionError("connection refused")

        reporter.register_check("cache", bad_check, critical=False)
        report = reporter.check_all()
        assert report["status"] == "degraded"
        assert report["checks"][0]["status"] == "degraded"
        assert "connection refused" in report["checks"][0]["message"]

    def test_non_critical_timeout_is_degraded(self):
        """A non-critical check that times out should be degraded."""
        reporter = HealthReporter("test")

        def hung():
            time.sleep(10)
            return True

        reporter.register_check("cache", hung, timeout=0.2, critical=False)
        report = reporter.check_all()
        assert report["status"] == "degraded"
        assert report["checks"][0]["status"] == "degraded"
        assert "timed out" in report["checks"][0]["message"].lower()

    def test_default_critical_is_true(self):
        """register_check without critical should default to True."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        _, _, critical = reporter._checks["db"]
        assert critical is True

    def test_critical_trumps_degraded(self):
        """If both critical and non-critical fail, overall should be unhealthy."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False, critical=True)
        reporter.register_check("cache", lambda: False, critical=False)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"

    def test_only_non_critical_failures_gives_degraded(self):
        """If only non-critical checks fail, overall should be degraded."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True, critical=True)
        reporter.register_check("cache", lambda: False, critical=False)
        reporter.register_check("metrics", lambda: False, critical=False)
        report = reporter.check_all()
        assert report["status"] == "degraded"

    def test_all_healthy_with_mixed_criticality(self):
        """All checks pass regardless of criticality = healthy."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True, critical=True)
        reporter.register_check("cache", lambda: True, critical=False)
        report = reporter.check_all()
        assert report["status"] == "healthy"

    def test_is_healthy_with_degraded(self):
        """is_healthy should return False when degraded."""
        reporter = HealthReporter("test")
        reporter.register_check("cache", lambda: False, critical=False)
        assert reporter.is_healthy() is False

    def test_multiple_non_critical_failures(self):
        """Multiple non-critical failures should all show degraded status."""
        reporter = HealthReporter("test")
        reporter.register_check("cache1", lambda: False, critical=False)
        reporter.register_check("cache2", lambda: False, critical=False)
        reporter.register_check("db", lambda: True, critical=True)
        report = reporter.check_all()
        assert report["status"] == "degraded"
        degraded = [c for c in report["checks"] if c["status"] == "degraded"]
        assert len(degraded) == 2


class TestBackwardCompatibility:
    def test_register_check_positional_only(self):
        """Existing code: register_check('name', fn) should still work."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: True)
        report = reporter.check_all()
        assert report["status"] == "healthy"

    def test_register_check_returns_false_still_unhealthy(self):
        """Existing behavior: False return = unhealthy (critical by default)."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        report = reporter.check_all()
        assert report["status"] == "unhealthy"

    def test_exception_still_unhealthy_by_default(self):
        """Existing behavior: exceptions = unhealthy (critical by default)."""
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        report = reporter.check_all()
        assert report["status"] == "unhealthy"
        assert "boom" in report["checks"][0]["message"]

    def test_unregister_still_works(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False, timeout=1.0, critical=True)
        reporter.unregister_check("db")
        assert reporter.is_healthy() is True

    def test_duration_still_tracked(self):
        reporter = HealthReporter("test")
        reporter.register_check("slow", lambda: (time.sleep(0.01) or True))
        report = reporter.check_all()
        assert report["checks"][0]["duration_ms"] >= 5

    def test_check_order_preserved(self):
        reporter = HealthReporter("test")
        names = ["alpha", "beta", "gamma"]
        for name in names:
            reporter.register_check(name, lambda: True)
        report = reporter.check_all()
        check_names = [c["name"] for c in report["checks"]]
        assert check_names == names

    def test_reregister_replaces(self):
        reporter = HealthReporter("test")
        reporter.register_check("db", lambda: False)
        reporter.register_check("db", lambda: True)
        report = reporter.check_all()
        assert report["status"] == "healthy"
        assert len(report["checks"]) == 1
