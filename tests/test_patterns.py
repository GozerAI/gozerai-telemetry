"""Tests for gozerai_telemetry.patterns — Bulkhead, RateLimiter, Timeout, FallbackChain."""

import threading
import time

import pytest

from gozerai_telemetry.patterns import Bulkhead, FallbackChain, RateLimiter, Timeout


# -- Bulkhead ----------------------------------------------------------------


class TestBulkhead:
    def test_acquire_release(self):
        bh = Bulkhead("test", max_concurrent=2)
        assert bh.acquire() is True
        assert bh.available == 1
        bh.release()
        assert bh.available == 2

    def test_context_manager(self):
        bh = Bulkhead("test", max_concurrent=2)
        with bh:
            assert bh.available == 1
        assert bh.available == 2

    def test_max_concurrent_blocks(self):
        bh = Bulkhead("test", max_concurrent=1)
        assert bh.acquire() is True
        # Second acquire should fail (non-blocking)
        assert bh.acquire() is False
        bh.release()

    def test_timeout_acquire(self):
        bh = Bulkhead("test", max_concurrent=1)
        assert bh.acquire() is True
        # With a short timeout, should still fail
        assert bh.acquire(timeout=0.05) is False
        bh.release()

    def test_stats(self):
        bh = Bulkhead("test-stats", max_concurrent=3)
        bh.acquire()
        bh.acquire()
        # Fail one
        bh.acquire()
        bh.acquire()  # rejected
        stats = bh.get_stats()
        assert stats["name"] == "test-stats"
        assert stats["max_concurrent"] == 3
        assert stats["available"] == 0
        assert stats["rejected"] == 1
        bh.release()
        bh.release()
        bh.release()

    def test_concurrent_access(self):
        bh = Bulkhead("concurrent", max_concurrent=5)
        results = []
        barrier = threading.Barrier(5)

        def worker():
            acquired = bh.acquire(timeout=2.0)
            if acquired:
                barrier.wait(timeout=2.0)
                results.append(True)
                bh.release()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(results) == 5
        assert bh.available == 5

    def test_context_manager_rejection_raises(self):
        bh = Bulkhead("test", max_concurrent=1)
        with bh:
            with pytest.raises(RuntimeError, match="rejected"):
                with bh:
                    pass  # pragma: no cover


# -- RateLimiter -------------------------------------------------------------


class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter("test", max_requests=5, window_seconds=10.0)
        for _ in range(5):
            assert rl.allow() is True

    def test_blocks_over_limit(self):
        rl = RateLimiter("test", max_requests=3, window_seconds=10.0)
        for _ in range(3):
            assert rl.allow() is True
        assert rl.allow() is False

    def test_window_slides(self):
        rl = RateLimiter("test", max_requests=2, window_seconds=0.1)
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is False
        # Wait for window to slide
        time.sleep(0.15)
        assert rl.allow() is True

    def test_wait_returns_zero_when_allowed(self):
        rl = RateLimiter("test", max_requests=5, window_seconds=10.0)
        assert rl.wait() == 0.0

    def test_wait_returns_positive_when_full(self):
        rl = RateLimiter("test", max_requests=1, window_seconds=1.0)
        rl.allow()
        wait_time = rl.wait()
        assert wait_time > 0.0
        assert wait_time <= 1.0

    def test_stats(self):
        rl = RateLimiter("stats-test", max_requests=10, window_seconds=60.0)
        rl.allow()
        rl.allow()
        stats = rl.get_stats()
        assert stats["name"] == "stats-test"
        assert stats["max_requests"] == 10
        assert stats["window_seconds"] == 60.0
        assert stats["current_count"] == 2


# -- Timeout -----------------------------------------------------------------


class TestTimeout:
    def test_completes_within_timeout(self):
        result = Timeout(2.0).execute(lambda: 42)
        assert result == 42

    def test_raises_on_timeout(self):
        def slow():
            time.sleep(5.0)
            return "done"  # pragma: no cover

        with pytest.raises(TimeoutError):
            Timeout(0.1).execute(slow)

    def test_returns_value(self):
        def add(a, b):
            return a + b

        result = Timeout(2.0).execute(add, 3, 7)
        assert result == 10

    def test_propagates_exception(self):
        def boom():
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            Timeout(2.0).execute(boom)


# -- FallbackChain -----------------------------------------------------------


class TestFallbackChain:
    def test_first_succeeds(self):
        chain = FallbackChain(lambda: "ok")
        assert chain.execute() == "ok"

    def test_first_fails_second_succeeds(self):
        def fail():
            raise RuntimeError("nope")

        chain = FallbackChain(fail, lambda: "backup")
        assert chain.execute() == "backup"

    def test_all_fail_raises(self):
        def fail1():
            raise RuntimeError("fail1")

        def fail2():
            raise ValueError("fail2")

        chain = FallbackChain(fail1, fail2)
        with pytest.raises(ValueError, match="fail2"):
            chain.execute()

    def test_execute_safe_returns_none(self):
        def fail():
            raise RuntimeError("nope")

        chain = FallbackChain(fail)
        assert chain.execute_safe() is None

    def test_empty_chain(self):
        chain = FallbackChain()
        with pytest.raises(RuntimeError, match="no callables"):
            chain.execute()

    def test_execute_safe_returns_value(self):
        chain = FallbackChain(lambda: "value")
        assert chain.execute_safe() == "value"
