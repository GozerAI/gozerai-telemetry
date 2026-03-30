"""Hedged requests -- send duplicate requests to multiple backends, use first response.

When latency variance is high, hedged requests reduce tail latency by
racing multiple backends. The first successful response wins; remaining
in-flight requests are abandoned (threads are daemon, so they won't block).

Zero external dependencies.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")


@dataclass
class HedgedResult:
    """Result of a hedged request execution."""
    value: Any = None
    backend_index: int = -1
    latency: float = 0.0
    success: bool = False
    error: Optional[str] = None
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "backend_index": self.backend_index,
            "latency": self.latency,
            "success": self.success,
            "error": self.error,
            "attempts": self.attempts,
        }


class HedgedRequest:
    """Send duplicate requests to multiple backends, use the first response.

    Each backend is represented by a callable that returns a value or raises.
    All callables are invoked concurrently (or with a stagger delay). The
    first successful result is returned; the rest are abandoned.

    Usage:
        hr = HedgedRequest(max_concurrency=3, stagger_delay=0.1)
        result = hr.execute([
            lambda: call_backend_a(),
            lambda: call_backend_b(),
            lambda: call_backend_c(),
        ])
        if result.success:
            print(f"Got response from backend {result.backend_index}")

    Stagger delay: if set, backends are launched ``stagger_delay`` seconds
    apart rather than all at once. This avoids unnecessary load when the
    first backend responds quickly.
    """

    def __init__(
        self,
        max_concurrency: int = 3,
        stagger_delay: float = 0.0,
        timeout: float = 10.0,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.stagger_delay = stagger_delay
        self.timeout = timeout
        self._lock = threading.Lock()
        self._total_executions = 0
        self._total_hedged = 0
        self._total_failures = 0

    def execute(self, backends: List[Callable[[], Any]]) -> HedgedResult:
        """Execute hedged request across the given backends.

        Returns the first successful result. If all fail, returns the
        last error.
        """
        if not backends:
            return HedgedResult(error="no backends provided")

        use_count = min(len(backends), self.max_concurrency)
        selected = backends[:use_count]

        with self._lock:
            self._total_executions += 1
            if use_count > 1:
                self._total_hedged += 1

        result_event = threading.Event()
        winner: List[HedgedResult] = []
        errors: List[str] = []
        finished_count = [0]
        count_lock = threading.Lock()
        start_time = time.monotonic()

        def _run_backend(idx: int, fn: Callable[[], Any]) -> None:
            try:
                value = fn()
                elapsed = time.monotonic() - start_time
                with count_lock:
                    if not winner:
                        winner.append(HedgedResult(
                            value=value,
                            backend_index=idx,
                            latency=elapsed,
                            success=True,
                            attempts=use_count,
                        ))
                        result_event.set()
                    finished_count[0] += 1
            except Exception as exc:
                with count_lock:
                    errors.append(f"backend[{idx}]: {exc}")
                    finished_count[0] += 1
                    if finished_count[0] >= use_count:
                        result_event.set()

        threads: List[threading.Thread] = []
        for i, fn in enumerate(selected):
            t = threading.Thread(target=_run_backend, args=(i, fn), daemon=True)
            threads.append(t)
            t.start()
            # Stagger: wait before launching next, unless we already have a result
            if self.stagger_delay > 0 and i < len(selected) - 1:
                result_event.wait(timeout=self.stagger_delay)
                if winner:
                    break

        # Wait for first result or timeout
        result_event.wait(timeout=self.timeout)

        if winner:
            return winner[0]

        with self._lock:
            self._total_failures += 1

        return HedgedResult(
            error="; ".join(errors) if errors else "all backends timed out",
            attempts=use_count,
        )

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_executions": self._total_executions,
                "total_hedged": self._total_hedged,
                "total_failures": self._total_failures,
                "max_concurrency": self.max_concurrency,
                "stagger_delay": self.stagger_delay,
                "timeout": self.timeout,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._total_executions = 0
            self._total_hedged = 0
            self._total_failures = 0
