"""Production resilience utilities — retry, circuit breaker, resilient HTTP.

Zero external dependencies. Works with both urllib (sync) and httpx (async).
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject calls
    HALF_OPEN = "half_open" # Testing recovery


@dataclass
class RetryPolicy:
    """Configurable retry with exponential backoff and jitter."""
    max_retries: int = 3
    base_delay: float = 1.0        # seconds
    max_delay: float = 30.0        # seconds cap
    jitter: bool = True            # add randomness to avoid thundering herd
    retryable_statuses: Set[int] = field(default_factory=lambda: {429, 502, 503, 504})
    retryable_exceptions: Tuple = field(default_factory=lambda: (ConnectionError, TimeoutError, OSError))

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate delay with exponential backoff + optional jitter."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        return delay

    def is_retryable_status(self, status: int) -> bool:
        return status in self.retryable_statuses

    def is_retryable_exception(self, exc: Exception) -> bool:
        return isinstance(exc, self.retryable_exceptions)


class CircuitBreaker:
    """Circuit breaker pattern — stops calling failing services.

    States:
      CLOSED  — normal, requests pass through
      OPEN    — service is down, requests fail immediately
      HALF_OPEN — testing if service recovered (1 request allowed)

    Transitions:
      CLOSED -> OPEN: after ``failure_threshold`` consecutive failures
      OPEN -> HALF_OPEN: after ``recovery_timeout`` seconds
      HALF_OPEN -> CLOSED: on success
      HALF_OPEN -> OPEN: on failure
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name or "unnamed"
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._success_count = 0
        self._total_requests = 0
        self._total_failures = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker '%s' entering half-open state", self.name)
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True  # Allow one probe request
        return False  # OPEN

    def record_success(self):
        """Record a successful request."""
        self._total_requests += 1
        self._success_count += 1
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("Circuit breaker '%s' recovered -> CLOSED", self.name)
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0  # Reset consecutive failures

    def record_failure(self):
        """Record a failed request."""
        self._total_requests += 1
        self._total_failures += 1
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker '%s' probe failed -> OPEN", self.name)
        elif self._state == CircuitState.CLOSED and self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker '%s' tripped after %d failures -> OPEN",
                self.name, self._failure_count,
            )

    def get_stats(self) -> Dict[str, Any]:
        """Return circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "success_count": self._success_count,
        }

    def reset(self):
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0


# -- Convenience defaults --------------------------------------------------

DEFAULT_RETRY = RetryPolicy()
CONSERVATIVE_RETRY = RetryPolicy(max_retries=2, base_delay=2.0)
AGGRESSIVE_RETRY = RetryPolicy(max_retries=5, base_delay=0.5)

# Global circuit breaker registry
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """Get or create a named circuit breaker (singleton per name)."""
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            name=name,
        )
    return _circuit_breakers[name]


def reset_all_breakers():
    """Reset all circuit breakers. Useful for testing."""
    _circuit_breakers.clear()


# -- Sync resilient fetch (urllib) ------------------------------------------


def resilient_fetch(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 5.0,
    retry_policy: Optional[RetryPolicy] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Optional[Any]:
    """GET url with retry + circuit breaker. Returns parsed JSON or None.

    Uses urllib (stdlib). Suitable for sync code in Nexus collectors.
    """
    policy = retry_policy or DEFAULT_RETRY

    if circuit_breaker and not circuit_breaker.allow_request():
        logger.debug("Circuit breaker '%s' is OPEN, skipping %s", circuit_breaker.name, url)
        return None

    last_exception = None
    for attempt in range(policy.max_retries + 1):
        try:
            req_headers = {"Accept": "application/json"}
            if headers:
                req_headers.update(headers)
            req = Request(url, headers=req_headers)
            with urlopen(req, timeout=timeout) as resp:
                status = resp.status
                if status >= 400:
                    if policy.is_retryable_status(status) and attempt < policy.max_retries:
                        delay = policy.delay_for_attempt(attempt)
                        logger.debug("Retryable status %d from %s, retry in %.1fs", status, url, delay)
                        time.sleep(delay)
                        continue
                    if circuit_breaker:
                        circuit_breaker.record_failure()
                    return None

                data = json.loads(resp.read().decode("utf-8"))
                if circuit_breaker:
                    circuit_breaker.record_success()
                return data

        except (URLError, OSError, ConnectionError, TimeoutError) as exc:
            last_exception = exc
            if attempt < policy.max_retries:
                delay = policy.delay_for_attempt(attempt)
                logger.debug("Request to %s failed (%s), retry %d in %.1fs", url, exc, attempt + 1, delay)
                time.sleep(delay)
                continue
        except Exception as exc:
            last_exception = exc
            logger.warning("Unexpected error fetching %s: %s", url, exc)
            break

    if circuit_breaker:
        circuit_breaker.record_failure()
    logger.warning("All retries exhausted for %s: %s", url, last_exception)
    return None


# -- Async resilient request (httpx) ---------------------------------------


async def resilient_request(
    method: str,
    url: str,
    *,
    json_body: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retry_policy: Optional[RetryPolicy] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Optional[Any]:
    """HTTP request with retry + circuit breaker. Returns parsed JSON or None.

    Uses httpx (must be installed separately). Suitable for async code in Arclane/TS.
    Returns parsed JSON response body, or None on failure.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed, cannot make async request to %s", url)
        return None

    policy = retry_policy or DEFAULT_RETRY

    if circuit_breaker and not circuit_breaker.allow_request():
        logger.debug("Circuit breaker '%s' is OPEN, skipping %s", circuit_breaker.name, url)
        return None

    import asyncio

    last_exception = None
    for attempt in range(policy.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                req_headers = {"Accept": "application/json"}
                if headers:
                    req_headers.update(headers)

                if method.upper() == "GET":
                    resp = await client.get(url, headers=req_headers)
                elif method.upper() == "POST":
                    resp = await client.post(url, json=json_body, headers=req_headers)
                else:
                    resp = await client.request(method, url, json=json_body, headers=req_headers)

                if resp.status_code >= 400:
                    if policy.is_retryable_status(resp.status_code) and attempt < policy.max_retries:
                        delay = policy.delay_for_attempt(attempt)
                        logger.debug("Retryable status %d from %s, retry in %.1fs", resp.status_code, url, delay)
                        await asyncio.sleep(delay)
                        continue
                    if circuit_breaker:
                        circuit_breaker.record_failure()
                    return None

                if circuit_breaker:
                    circuit_breaker.record_success()
                return resp.json()

        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as exc:
            last_exception = exc
            if attempt < policy.max_retries:
                delay = policy.delay_for_attempt(attempt)
                logger.debug("Request to %s failed (%s), retry %d in %.1fs", url, exc, attempt + 1, delay)
                await asyncio.sleep(delay)
                continue
        except Exception as exc:
            last_exception = exc
            logger.warning("Unexpected error requesting %s: %s", url, exc)
            break

    if circuit_breaker:
        circuit_breaker.record_failure()
    logger.warning("All retries exhausted for %s: %s", url, last_exception)
    return None
