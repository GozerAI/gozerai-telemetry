"""Tests for gozerai_telemetry.slo — SLI, SLO, SLOTracker."""

import threading

from gozerai_telemetry.slo import SLI, SLO, SLOTracker


# -- SLI ---------------------------------------------------------------------


class TestSLI:
    def test_initial_ratio(self):
        sli = SLI(name="test")
        assert sli.ratio == 0.0

    def test_record_good(self):
        sli = SLI(name="test")
        sli.record_good()
        assert sli.ratio == 1.0

    def test_record_bad(self):
        sli = SLI(name="test")
        sli.record_bad()
        assert sli.ratio == 0.0

    def test_ratio_calculation(self):
        sli = SLI(name="test")
        for _ in range(9):
            sli.record_good()
        sli.record_bad()
        assert abs(sli.ratio - 0.9) < 1e-9

    def test_reset(self):
        sli = SLI(name="test")
        sli.record_good()
        sli.record_good()
        sli.reset()
        assert sli.ratio == 0.0

    def test_concurrent_recording(self):
        sli = SLI(name="concurrent")
        n_per_thread = 1000

        def record_goods():
            for _ in range(n_per_thread):
                sli.record_good()

        def record_bads():
            for _ in range(n_per_thread):
                sli.record_bad()

        threads = [
            threading.Thread(target=record_goods),
            threading.Thread(target=record_goods),
            threading.Thread(target=record_bads),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 2000 good + 1000 bad = 3000 total
        expected_ratio = 2000 / 3000
        assert abs(sli.ratio - expected_ratio) < 1e-9


# -- SLO ---------------------------------------------------------------------


class TestSLO:
    def test_met_above_target(self):
        sli = SLI(name="test")
        for _ in range(100):
            sli.record_good()
        slo = SLO(name="availability", sli=sli, target=0.99)
        assert slo.met is True

    def test_not_met_below_target(self):
        sli = SLI(name="test")
        for _ in range(90):
            sli.record_good()
        for _ in range(10):
            sli.record_bad()
        slo = SLO(name="availability", sli=sli, target=0.95)
        assert slo.met is False

    def test_error_budget_remaining(self):
        sli = SLI(name="test")
        for _ in range(999):
            sli.record_good()
        sli.record_bad()
        slo = SLO(name="availability", sli=sli, target=0.999)
        # ratio = 0.999, target = 0.999 -> budget = 0.0
        assert abs(slo.error_budget_remaining) < 1e-9

    def test_error_budget_negative(self):
        sli = SLI(name="test")
        for _ in range(90):
            sli.record_good()
        for _ in range(10):
            sli.record_bad()
        slo = SLO(name="availability", sli=sli, target=0.999)
        # ratio=0.9, target=0.999 -> budget = 0.9 - 0.999 = -0.099
        assert slo.error_budget_remaining < 0

    def test_to_dict(self):
        sli = SLI(name="test")
        sli.record_good()
        slo = SLO(name="latency", sli=sli, target=0.95)
        d = slo.to_dict()
        assert d["name"] == "latency"
        assert d["target"] == 0.95
        assert d["current_ratio"] == 1.0
        assert d["met"] is True
        assert "error_budget_remaining" in d


# -- SLOTracker --------------------------------------------------------------


class TestSLOTracker:
    def test_register_and_get(self):
        tracker = SLOTracker("my-service")
        slo = tracker.register("availability", 0.999)
        assert slo.name == "availability"
        assert tracker.get("availability") is slo

    def test_report(self):
        tracker = SLOTracker("my-service")
        slo = tracker.register("availability", 0.999)
        slo.sli.record_good()
        report = tracker.report()
        assert report["service"] == "my-service"
        assert "availability" in report["slos"]
        assert report["all_met"] is True

    def test_get_nonexistent_returns_none(self):
        tracker = SLOTracker("my-service")
        assert tracker.get("nope") is None

    def test_multiple_slos(self):
        tracker = SLOTracker("my-service")
        avail = tracker.register("availability", 0.999)
        latency = tracker.register("latency", 0.95)

        # availability: all good
        for _ in range(100):
            avail.sli.record_good()

        # latency: 90% good -> below 0.95
        for _ in range(90):
            latency.sli.record_good()
        for _ in range(10):
            latency.sli.record_bad()

        report = tracker.report()
        assert report["all_met"] is False
        assert report["slos"]["availability"]["met"] is True
        assert report["slos"]["latency"]["met"] is False
