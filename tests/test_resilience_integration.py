"""Integration tests for resilience patterns across the GozerAI ecosystem.

Verifies end-to-end behavior of retry, circuit breaker, and graceful
degradation using real connections to unreachable ports (no mocks where feasible).
"""

import asyncio
import time

import pytest

from gozerai_telemetry.resilience import (
    CircuitBreaker,
    CircuitState,
    RetryPolicy,
    get_circuit_breaker,
    reset_all_breakers,
    resilient_fetch,
    resilient_request,
)

# Unreachable address for real connection-failure tests
UNREACHABLE_URL = "http://localhost:59999/does-not-exist"


class TestCircuitBreakerIntegration:
    """End-to-end circuit breaker lifecycle tests."""

    def setup_method(self):
        reset_all_breakers()

    def test_opens_after_n_failures_then_rejects_fast(self):
        """CB opens after threshold failures, then subsequent calls are rejected
        immediately (no network delay)."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60, name="fast-reject")

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN

        # Rejection should be near-instant (no I/O)
        start = time.monotonic()
        result = resilient_fetch(
            UNREACHABLE_URL,
            circuit_breaker=cb,
            retry_policy=RetryPolicy(max_retries=0),
        )
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 0.05  # Fast rejection, no network call

    def test_transitions_to_half_open_after_recovery_timeout(self):
        """CB moves from OPEN to HALF_OPEN once recovery_timeout elapses."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="half-open-test")
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request()

    def test_half_open_resets_to_closed_on_success(self):
        """A successful probe in HALF_OPEN returns the CB to CLOSED."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="reset-test")
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_multiple_breakers_are_independent(self):
        """Failing one named breaker does not affect another."""
        cb_a = get_circuit_breaker("service-a", failure_threshold=2)
        cb_b = get_circuit_breaker("service-b", failure_threshold=2)

        cb_a.record_failure()
        cb_a.record_failure()
        assert cb_a.state == CircuitState.OPEN

        # service-b should be unaffected
        assert cb_b.state == CircuitState.CLOSED
        assert cb_b.allow_request()

    def test_reset_all_breakers_clears_state(self):
        """reset_all_breakers() removes all registered breakers so fresh ones
        are created on next access."""
        cb = get_circuit_breaker("will-be-reset", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        reset_all_breakers()

        cb_new = get_circuit_breaker("will-be-reset", failure_threshold=1)
        assert cb_new is not cb
        assert cb_new.state == CircuitState.CLOSED


class TestResilientFetchIntegration:
    """Tests using real (failing) network connections."""

    def setup_method(self):
        reset_all_breakers()

    def test_returns_none_on_real_connection_failure(self):
        """resilient_fetch returns None when the target is genuinely unreachable."""
        policy = RetryPolicy(max_retries=0)
        result = resilient_fetch(UNREACHABLE_URL, retry_policy=policy, timeout=0.5)
        assert result is None

    def test_retry_actually_retries(self):
        """Verify that the retry loop executes the expected number of attempts
        against an unreachable host."""
        call_count = 0
        original_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

        def counting_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_urlopen(*args, **kwargs)

        policy = RetryPolicy(max_retries=2, base_delay=0.01, jitter=False)
        from unittest.mock import patch

        with patch("gozerai_telemetry.resilience._base.urlopen", side_effect=counting_urlopen):
            result = resilient_fetch(
                UNREACHABLE_URL, retry_policy=policy, timeout=0.5,
            )

        assert result is None
        assert call_count == 3  # 1 initial + 2 retries

    def test_retry_policy_max_retries_zero_no_retry(self):
        """With max_retries=0, only one attempt is made."""
        call_count = 0

        def counting_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refused")

        policy = RetryPolicy(max_retries=0)
        from unittest.mock import patch

        with patch("gozerai_telemetry.resilience._base.urlopen", side_effect=counting_urlopen):
            result = resilient_fetch(UNREACHABLE_URL, retry_policy=policy)

        assert result is None
        assert call_count == 1


class TestResilientRequestIntegration:
    """Async resilient_request tests."""

    def setup_method(self):
        reset_all_breakers()

    def test_async_returns_none_on_connection_failure(self):
        """resilient_request returns None when target is unreachable."""
        policy = RetryPolicy(max_retries=0)

        async def _run():
            return await resilient_request(
                "GET", UNREACHABLE_URL,
                retry_policy=policy, timeout=0.5,
            )

        result = asyncio.run(_run())
        # If httpx is not installed, result is also None (graceful degradation)
        assert result is None
