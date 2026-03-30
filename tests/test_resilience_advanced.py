"""Tests for advanced resilience: adaptive timeout, hedged requests, load shedding."""

import time
import threading
import pytest

from gozerai_telemetry.resilience import AdaptiveTimeout, HedgedRequest, HedgedResult, LoadShedder, ShedDecision


class TestAdaptiveTimeout:

    def test_initial_timeout(self):
        at = AdaptiveTimeout(initial_timeout=5.0)
        assert at.get_timeout() == 5.0

    def test_returns_initial_when_few_samples(self):
        at = AdaptiveTimeout(initial_timeout=3.0, min_samples=10)
        for _ in range(5):
            at.record(0.1)
        assert at.get_timeout() == 3.0

    def test_adapts_after_enough_samples(self):
        at = AdaptiveTimeout(initial_timeout=5.0, min_samples=5, multiplier=3.0, percentile=95.0)
        for _ in range(20):
            at.record(0.1)
        timeout = at.get_timeout()
        assert timeout < 5.0
        assert timeout >= at.min_timeout

    def test_high_latency_increases_timeout(self):
        at = AdaptiveTimeout(initial_timeout=1.0, min_samples=5, multiplier=2.0, percentile=95.0)
        for _ in range(20):
            at.record(2.0)
        timeout = at.get_timeout()
        assert timeout >= 2.0

    def test_clamped_to_max(self):
        at = AdaptiveTimeout(max_timeout=10.0, min_samples=5, multiplier=100.0)
        for _ in range(20):
            at.record(5.0)
        assert at.get_timeout() == 10.0

    def test_clamped_to_min(self):
        at = AdaptiveTimeout(min_timeout=1.0, min_samples=5, multiplier=0.01)
        for _ in range(20):
            at.record(0.001)
        assert at.get_timeout() == 1.0

    def test_percentiles(self):
        at = AdaptiveTimeout()
        assert at.get_percentiles() == {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        for i in range(100):
            at.record(float(i))
        p = at.get_percentiles()
        assert p["p50"] > 0
        assert p["p95"] > p["p50"]
        assert p["p99"] >= p["p95"]

    def test_stats(self):
        at = AdaptiveTimeout(min_samples=5)
        for _ in range(10):
            at.record(0.1)
        at.record(0.5, success=False)
        stats = at.get_stats()
        assert stats["total_samples"] == 11
        assert stats["total_timeouts"] == 1
        assert stats["sample_count"] >= 10

    def test_reset(self):
        at = AdaptiveTimeout(min_samples=5)
        for _ in range(20):
            at.record(0.1)
        at.reset()
        assert at.get_timeout() == at.initial_timeout

    def test_window_size_limit(self):
        at = AdaptiveTimeout(window_size=10, min_samples=5)
        for _ in range(50):
            at.record(0.1)
        stats = at.get_stats()
        assert stats["sample_count"] <= 10


class TestHedgedRequest:

    def test_single_backend_success(self):
        hr = HedgedRequest()
        result = hr.execute([lambda: 42])
        assert result.success is True
        assert result.value == 42
        assert result.backend_index == 0

    def test_first_fast_response_wins(self):
        def fast():
            return "fast"
        def slow():
            time.sleep(2)
            return "slow"
        hr = HedgedRequest(timeout=3.0)
        result = hr.execute([fast, slow])
        assert result.success is True
        assert result.value == "fast"

    def test_fallback_to_second_backend(self):
        def fail():
            raise ConnectionError("down")
        def succeed():
            return "ok"
        hr = HedgedRequest(timeout=3.0)
        result = hr.execute([fail, succeed])
        assert result.success is True
        assert result.value == "ok"

    def test_all_fail(self):
        def fail():
            raise ConnectionError("down")
        hr = HedgedRequest(timeout=2.0)
        result = hr.execute([fail, fail])
        assert result.success is False
        assert result.error is not None

    def test_empty_backends(self):
        hr = HedgedRequest()
        result = hr.execute([])
        assert result.success is False

    def test_max_concurrency(self):
        hr = HedgedRequest(max_concurrency=2)
        results_list = []
        def b():
            results_list.append(1)
            return "ok"
        result = hr.execute([b, b, b, b, b])
        assert result.success is True
        assert result.attempts == 2

    def test_stats(self):
        hr = HedgedRequest()
        hr.execute([lambda: 1])
        hr.execute([lambda: 2, lambda: 3])
        stats = hr.get_stats()
        assert stats["total_executions"] == 2
        assert stats["total_hedged"] == 1

    def test_reset_stats(self):
        hr = HedgedRequest()
        hr.execute([lambda: 1])
        hr.reset_stats()
        assert hr.get_stats()["total_executions"] == 0

    def test_result_to_dict(self):
        r = HedgedResult(value=42, backend_index=0, latency=0.1, success=True, attempts=2)
        d = r.to_dict()
        assert d["value"] == 42
        assert d["success"] is True


class TestLoadShedder:

    def test_admit_when_tokens_available(self):
        ls = LoadShedder(max_tokens=100, refill_rate=0)
        assert ls.check() == ShedDecision.ADMIT

    def test_shed_when_no_tokens(self):
        ls = LoadShedder(max_tokens=2, refill_rate=0)
        ls.check()
        ls.check()
        assert ls.check() == ShedDecision.SHED

    def test_degraded_near_threshold(self):
        ls = LoadShedder(max_tokens=10, refill_rate=0, degrade_threshold=0.3)
        for _ in range(8):
            ls.check()
        decision = ls.check()
        assert decision == ShedDecision.DEGRADED

    def test_refill_over_time(self):
        ls = LoadShedder(max_tokens=10, refill_rate=1000.0)
        for _ in range(10):
            ls.check()
        time.sleep(0.05)
        assert ls.check() != ShedDecision.SHED

    def test_try_acquire(self):
        ls = LoadShedder(max_tokens=1, refill_rate=0)
        assert ls.try_acquire() is True
        assert ls.try_acquire() is False

    def test_utilization(self):
        ls = LoadShedder(max_tokens=10, refill_rate=0)
        assert ls.utilization == 0.0
        for _ in range(5):
            ls.check()
        assert 0.4 < ls.utilization < 0.6

    def test_available_tokens(self):
        ls = LoadShedder(max_tokens=100, refill_rate=0)
        initial = ls.available_tokens
        ls.check()
        assert ls.available_tokens < initial

    def test_stats(self):
        ls = LoadShedder(max_tokens=5, refill_rate=0, degrade_threshold=0.3)
        for _ in range(7):
            ls.check()
        stats = ls.get_stats()
        assert stats.total_requests == 7
        assert stats.shed > 0
        d = stats.to_dict()
        assert "shed_rate" in d

    def test_reset(self):
        ls = LoadShedder(max_tokens=5, refill_rate=0)
        for _ in range(5):
            ls.check()
        ls.reset()
        assert ls.check() == ShedDecision.ADMIT

    def test_context_manager_admit(self):
        ls = LoadShedder(max_tokens=100, refill_rate=0)
        with ls as decision:
            assert decision in (ShedDecision.ADMIT, ShedDecision.DEGRADED)

    def test_context_manager_reject(self):
        ls = LoadShedder(max_tokens=1, refill_rate=0)
        ls.check()
        with pytest.raises(LoadShedder.Rejected):
            with ls:
                pass

    def test_custom_cost(self):
        ls = LoadShedder(max_tokens=10, refill_rate=0)
        ls.check(cost=8)
        assert ls.check(cost=5) == ShedDecision.SHED
