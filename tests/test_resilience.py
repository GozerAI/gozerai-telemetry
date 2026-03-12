"""Tests for resilience module — retry, circuit breaker, resilient HTTP."""

import json
import time
import pytest
from unittest.mock import MagicMock, patch

from gozerai_telemetry.resilience import (
    RetryPolicy,
    CircuitBreaker,
    CircuitState,
    resilient_fetch,
    get_circuit_breaker,
    reset_all_breakers,
    DEFAULT_RETRY,
    CONSERVATIVE_RETRY,
    AGGRESSIVE_RETRY,
)


# -- RetryPolicy -----------------------------------------------------------


class TestRetryPolicy:
    def test_default_values(self):
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.base_delay == 1.0
        assert p.max_delay == 30.0
        assert p.jitter is True

    def test_delay_exponential_backoff(self):
        p = RetryPolicy(jitter=False)
        assert p.delay_for_attempt(0) == 1.0
        assert p.delay_for_attempt(1) == 2.0
        assert p.delay_for_attempt(2) == 4.0
        assert p.delay_for_attempt(3) == 8.0

    def test_delay_capped_at_max(self):
        p = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=False)
        assert p.delay_for_attempt(10) == 5.0

    def test_delay_with_jitter(self):
        p = RetryPolicy(jitter=True)
        delays = [p.delay_for_attempt(0) for _ in range(20)]
        # With jitter, delays should vary (0.5 to 1.0 of base)
        assert min(delays) < max(delays)
        assert all(0.5 <= d <= 1.0 for d in delays)

    def test_retryable_status(self):
        p = RetryPolicy()
        assert p.is_retryable_status(502)
        assert p.is_retryable_status(503)
        assert p.is_retryable_status(504)
        assert p.is_retryable_status(429)
        assert not p.is_retryable_status(400)
        assert not p.is_retryable_status(404)
        assert not p.is_retryable_status(500)

    def test_retryable_exception(self):
        p = RetryPolicy()
        assert p.is_retryable_exception(ConnectionError("test"))
        assert p.is_retryable_exception(TimeoutError("test"))
        assert p.is_retryable_exception(OSError("test"))
        assert not p.is_retryable_exception(ValueError("test"))

    def test_custom_retryable_statuses(self):
        p = RetryPolicy(retryable_statuses={500, 502})
        assert p.is_retryable_status(500)
        assert not p.is_retryable_status(503)


# -- CircuitBreaker --------------------------------------------------------


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED
        assert not cb.is_open
        assert cb.allow_request()

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open
        assert not cb.allow_request()

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, name="test")
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # Reset
        cb.record_failure()
        cb.record_failure()
        # Should still be closed (only 2 consecutive failures)
        assert cb.state == CircuitState.CLOSED

    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="test")
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request()  # One probe allowed

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="test")
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, name="test")
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_get_stats(self):
        cb = CircuitBreaker(name="test-svc")
        cb.record_success()
        cb.record_failure()
        stats = cb.get_stats()
        assert stats["name"] == "test-svc"
        assert stats["total_requests"] == 2
        assert stats["total_failures"] == 1
        assert stats["success_count"] == 1

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1, name="test")
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request()

    def test_state_enum_values(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_default_name(self):
        cb = CircuitBreaker()
        assert cb.name == "unnamed"

    def test_failure_count_tracks_consecutive(self):
        cb = CircuitBreaker(failure_threshold=5, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2
        cb.record_success()
        assert cb._failure_count == 0
        cb.record_failure()
        assert cb._failure_count == 1


# -- Circuit breaker registry ----------------------------------------------


class TestCircuitBreakerRegistry:
    def setup_method(self):
        reset_all_breakers()

    def test_get_creates_new(self):
        cb = get_circuit_breaker("svc-a")
        assert cb.name == "svc-a"

    def test_get_returns_same_instance(self):
        cb1 = get_circuit_breaker("svc-a")
        cb2 = get_circuit_breaker("svc-a")
        assert cb1 is cb2

    def test_different_names_different_instances(self):
        cb1 = get_circuit_breaker("svc-a")
        cb2 = get_circuit_breaker("svc-b")
        assert cb1 is not cb2

    def test_reset_all(self):
        get_circuit_breaker("a")
        get_circuit_breaker("b")
        reset_all_breakers()
        # New call should create fresh instance
        cb = get_circuit_breaker("a")
        assert cb.state == CircuitState.CLOSED

    def test_custom_params(self):
        cb = get_circuit_breaker("svc-c", failure_threshold=10, recovery_timeout=120.0)
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 120.0


# -- resilient_fetch -------------------------------------------------------


def _mock_urlopen_response(data, status=200):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestResilientFetch:
    def setup_method(self):
        reset_all_breakers()

    def test_success(self):
        with patch("gozerai_telemetry.resilience.urlopen",
                    return_value=_mock_urlopen_response({"ok": True})):
            result = resilient_fetch("http://test/api")
            assert result == {"ok": True}

    def test_retries_on_connection_error(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            return _mock_urlopen_response({"ok": True})

        policy = RetryPolicy(max_retries=3, base_delay=0.01, jitter=False)
        with patch("gozerai_telemetry.resilience.urlopen", side_effect=side_effect):
            result = resilient_fetch("http://test/api", retry_policy=policy)
            assert result == {"ok": True}
            assert call_count == 3

    def test_returns_none_after_exhausted_retries(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.01, jitter=False)
        with patch("gozerai_telemetry.resilience.urlopen",
                    side_effect=ConnectionError("down")):
            result = resilient_fetch("http://test/api", retry_policy=policy)
            assert result is None

    def test_circuit_breaker_blocks_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, name="test")
        cb.record_failure()  # Open the breaker

        result = resilient_fetch("http://test/api", circuit_breaker=cb)
        assert result is None

    def test_circuit_breaker_records_success(self):
        cb = CircuitBreaker(name="test")
        with patch("gozerai_telemetry.resilience.urlopen",
                    return_value=_mock_urlopen_response({"ok": True})):
            resilient_fetch("http://test/api", circuit_breaker=cb)
            assert cb._success_count == 1

    def test_circuit_breaker_records_failure(self):
        cb = CircuitBreaker(name="test")
        policy = RetryPolicy(max_retries=0)
        with patch("gozerai_telemetry.resilience.urlopen",
                    side_effect=ConnectionError("down")):
            resilient_fetch("http://test/api", circuit_breaker=cb, retry_policy=policy)
            assert cb._total_failures == 1

    def test_custom_headers(self):
        with patch("gozerai_telemetry.resilience.urlopen",
                    return_value=_mock_urlopen_response({"ok": True})) as mock_open:
            resilient_fetch(
                "http://test/api",
                headers={"Authorization": "Bearer token123"},
            )
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == "Bearer token123"

    def test_no_retry_on_non_retryable_exception(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("bad")

        policy = RetryPolicy(max_retries=3, base_delay=0.01)
        with patch("gozerai_telemetry.resilience.urlopen", side_effect=side_effect):
            result = resilient_fetch("http://test/api", retry_policy=policy)
            assert result is None
            assert call_count == 1  # No retries for ValueError

    def test_default_accept_header(self):
        with patch("gozerai_telemetry.resilience.urlopen",
                    return_value=_mock_urlopen_response({"ok": True})) as mock_open:
            resilient_fetch("http://test/api")
            req = mock_open.call_args[0][0]
            assert req.get_header("Accept") == "application/json"

    def test_timeout_passed_to_urlopen(self):
        with patch("gozerai_telemetry.resilience.urlopen",
                    return_value=_mock_urlopen_response({"ok": True})) as mock_open:
            resilient_fetch("http://test/api", timeout=15.0)
            assert mock_open.call_args[1]["timeout"] == 15.0


# -- Presets ---------------------------------------------------------------


class TestPresets:
    def test_default_retry(self):
        assert DEFAULT_RETRY.max_retries == 3

    def test_conservative_retry(self):
        assert CONSERVATIVE_RETRY.max_retries == 2
        assert CONSERVATIVE_RETRY.base_delay == 2.0

    def test_aggressive_retry(self):
        assert AGGRESSIVE_RETRY.max_retries == 5
        assert AGGRESSIVE_RETRY.base_delay == 0.5
