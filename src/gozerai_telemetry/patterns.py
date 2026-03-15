"""Additional resilience patterns — bulkhead, rate limiter, timeout, fallback.

Zero dependencies. Thread-safe. Compatible with the existing resilience module.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class Bulkhead:
    """Limits concurrent access to a resource (semaphore pattern).

    Usage:
        bh = Bulkhead("db-pool", max_concurrent=5)
        with bh:
            do_db_work()
    """

    def __init__(self, name: str, max_concurrent: int = 10) -> None:
        self.name = name
        self._max_concurrent = max_concurrent
        self._semaphore = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._active = 0
        self._rejected = 0

    def acquire(self, timeout: float = 0) -> bool:
        """Try to acquire a slot. Returns True on success."""
        if timeout > 0:
            acquired = self._semaphore.acquire(timeout=timeout)
        else:
            acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._active += 1
            return True
        with self._lock:
            self._rejected += 1
        return False

    def release(self) -> None:
        """Release a previously acquired slot."""
        with self._lock:
            self._active -= 1
        self._semaphore.release()

    def __enter__(self) -> Bulkhead:
        if not self.acquire():
            with self._lock:
                # acquire already counted rejected, just raise
                pass
            raise RuntimeError(f"Bulkhead '{self.name}' rejected: max concurrency reached")
        return self

    def __exit__(self, *args) -> None:
        self.release()

    @property
    def available(self) -> int:
        with self._lock:
            return self._max_concurrent - self._active

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "max_concurrent": self._max_concurrent,
                "available": self._max_concurrent - self._active,
                "rejected": self._rejected,
            }


class RateLimiter:
    """Sliding-window rate limiter.

    Usage:
        rl = RateLimiter("api", max_requests=100, window_seconds=60.0)
        if rl.allow():
            make_request()
    """

    def __init__(self, name: str, max_requests: int, window_seconds: float = 60.0) -> None:
        self.name = name
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def allow(self) -> bool:
        """Return True if request is allowed under the rate limit."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True
            return False

    def wait(self) -> float:
        """Seconds until the next request would be allowed. 0 if allowed now."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._timestamps) < self.max_requests:
                return 0.0
            # Oldest timestamp will expire at oldest + window
            oldest = self._timestamps[0]
            return max(0.0, (oldest + self.window_seconds) - now)

    def get_stats(self) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return {
                "name": self.name,
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
                "current_count": len(self._timestamps),
            }


class Timeout:
    """Wraps a callable with a timeout using threading.

    Uses Thread + Event pattern (Windows-compatible, no signals).

    Usage:
        result = Timeout(5.0).execute(slow_function, arg1, arg2)
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def execute(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Execute fn with a timeout. Raises TimeoutError if it exceeds the limit."""
        result: list = []
        exception: list = []
        completed = threading.Event()

        def _worker():
            try:
                result.append(fn(*args, **kwargs))
            except Exception as exc:
                exception.append(exc)
            finally:
                completed.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        if not completed.wait(timeout=self.seconds):
            raise TimeoutError(
                f"Operation did not complete within {self.seconds}s"
            )

        if exception:
            raise exception[0]
        return result[0]


class FallbackChain:
    """Try a chain of callables, return the first successful result.

    Usage:
        result = FallbackChain(primary_fn, secondary_fn, tertiary_fn).execute()
    """

    def __init__(self, *fns: Callable[[], T]) -> None:
        self._fns = fns

    def execute(self) -> T:
        """Try each fn in order. Returns first success. Raises last exception if all fail."""
        if not self._fns:
            raise RuntimeError("FallbackChain has no callables")
        last_exc: Optional[Exception] = None
        for fn in self._fns:
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
        raise last_exc  # type: ignore[misc]

    def execute_safe(self) -> Optional[T]:
        """Try each fn in order. Returns first success or None if all fail."""
        try:
            return self.execute()
        except Exception:
            return None
