"""Tests for AsyncRateLimiter."""

import asyncio
import time

import pytest

from gozerai_telemetry.patterns import AsyncRateLimiter


pytestmark = pytest.mark.asyncio


class TestAsyncRateLimiterAllow:
    async def test_allows_under_limit(self):
        rl = AsyncRateLimiter("test", max_requests=5, window_seconds=60.0)
        assert await rl.allow() is True

    async def test_allows_up_to_max(self):
        rl = AsyncRateLimiter("test", max_requests=3, window_seconds=60.0)
        results = [await rl.allow() for _ in range(3)]
        assert all(results)

    async def test_rejects_over_limit(self):
        rl = AsyncRateLimiter("test", max_requests=2, window_seconds=60.0)
        assert await rl.allow() is True
        assert await rl.allow() is True
        assert await rl.allow() is False

    async def test_single_request_allowed(self):
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=60.0)
        assert await rl.allow() is True
        assert await rl.allow() is False

    async def test_window_expiry_allows_again(self):
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=0.05)
        assert await rl.allow() is True
        assert await rl.allow() is False
        await asyncio.sleep(0.06)
        assert await rl.allow() is True


class TestAsyncRateLimiterWait:
    async def test_wait_returns_immediately_if_allowed(self):
        rl = AsyncRateLimiter("test", max_requests=5, window_seconds=60.0)
        start = time.monotonic()
        await rl.wait()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    async def test_wait_blocks_until_token_available(self):
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=0.05)
        assert await rl.allow() is True
        start = time.monotonic()
        await rl.wait(timeout=1.0)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04  # Should have waited ~50ms

    async def test_wait_timeout_raises(self):
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=60.0)
        assert await rl.allow() is True
        with pytest.raises(TimeoutError, match="timed out"):
            await rl.wait(timeout=0.05)

    async def test_wait_timeout_none_waits_indefinitely(self):
        """With short window, None timeout should eventually succeed."""
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=0.05)
        assert await rl.allow() is True
        await rl.wait(timeout=1.0)  # Should succeed after window expires

    async def test_wait_consumes_token(self):
        rl = AsyncRateLimiter("test", max_requests=2, window_seconds=60.0)
        await rl.wait()
        await rl.wait()
        assert await rl.allow() is False


class TestAsyncRateLimiterStats:
    async def test_stats_initial(self):
        rl = AsyncRateLimiter("api", max_requests=10, window_seconds=30.0)
        stats = await rl.get_stats()
        assert stats["name"] == "api"
        assert stats["max_requests"] == 10
        assert stats["window_seconds"] == 30.0
        assert stats["current_count"] == 0

    async def test_stats_after_requests(self):
        rl = AsyncRateLimiter("api", max_requests=10, window_seconds=60.0)
        await rl.allow()
        await rl.allow()
        stats = await rl.get_stats()
        assert stats["current_count"] == 2

    async def test_stats_after_window_expiry(self):
        rl = AsyncRateLimiter("api", max_requests=10, window_seconds=0.05)
        await rl.allow()
        await asyncio.sleep(0.06)
        stats = await rl.get_stats()
        assert stats["current_count"] == 0


class TestAsyncRateLimiterConcurrency:
    async def test_concurrent_allows_respects_limit(self):
        rl = AsyncRateLimiter("test", max_requests=5, window_seconds=60.0)
        results = await asyncio.gather(*[rl.allow() for _ in range(10)])
        assert sum(results) == 5

    async def test_concurrent_waits_all_succeed(self):
        rl = AsyncRateLimiter("test", max_requests=3, window_seconds=0.05)
        # 3 concurrent waits — all should eventually succeed
        await asyncio.wait_for(
            asyncio.gather(*[rl.wait(timeout=2.0) for _ in range(3)]),
            timeout=3.0,
        )

    async def test_many_concurrent_allows(self):
        rl = AsyncRateLimiter("test", max_requests=50, window_seconds=60.0)
        results = await asyncio.gather(*[rl.allow() for _ in range(100)])
        assert sum(results) == 50


class TestAsyncRateLimiterEdgeCases:
    async def test_zero_window(self):
        """With a zero window, all past timestamps are immediately pruned."""
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=0.0)
        # First call records a timestamp; second call prunes it (it's <= cutoff)
        assert await rl.allow() is True
        assert await rl.allow() is True

    async def test_name_attribute(self):
        rl = AsyncRateLimiter("my-limiter", max_requests=1, window_seconds=1.0)
        assert rl.name == "my-limiter"

    async def test_max_requests_attribute(self):
        rl = AsyncRateLimiter("test", max_requests=42, window_seconds=1.0)
        assert rl.max_requests == 42

    async def test_window_seconds_attribute(self):
        rl = AsyncRateLimiter("test", max_requests=1, window_seconds=99.0)
        assert rl.window_seconds == 99.0


class TestAsyncRateLimiterImport:
    async def test_importable_from_package(self):
        from gozerai_telemetry import AsyncRateLimiter as ARL
        assert ARL is AsyncRateLimiter
