"""Token-bucket based load shedding.

Rejects incoming requests when the system is overloaded, preventing
cascade failures. Uses a token bucket algorithm: tokens refill at a
steady rate and each request consumes one token. When tokens are
exhausted, requests are shed (rejected with 503-equivalent).

Zero external dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Dict


class ShedDecision(Enum):
    """Result of a load shedding check."""
    ADMIT = "admit"       # Request is allowed through
    SHED = "shed"         # Request is rejected (overloaded)
    DEGRADED = "degraded" # Allowed but system is under pressure


@dataclass
class ShedStats:
    """Accumulated load shedding statistics."""
    total_requests: int = 0
    admitted: int = 0
    shed: int = 0
    degraded: int = 0
    current_tokens: float = 0.0
    max_tokens: float = 0.0
    refill_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "admitted": self.admitted,
            "shed": self.shed,
            "degraded": self.degraded,
            "current_tokens": round(self.current_tokens, 2),
            "max_tokens": self.max_tokens,
            "refill_rate": self.refill_rate,
            "shed_rate": (self.shed / self.total_requests * 100.0)
                if self.total_requests > 0 else 0.0,
        }


class LoadShedder:
    """Token-bucket based load shedder.

    Each request consumes 1 token. Tokens refill at ``refill_rate`` per second
    up to ``max_tokens``. When tokens fall below ``degrade_threshold``, requests
    are admitted but marked as degraded. When tokens hit zero, requests are shed.

    Usage:
        shedder = LoadShedder(max_tokens=100, refill_rate=50.0)
        decision = shedder.check()
        if decision == ShedDecision.SHED:
            return Response(status=503)
        elif decision == ShedDecision.DEGRADED:
            # Serve with reduced functionality
            ...
        else:
            # Normal service
            ...

    Context manager usage:
        shedder = LoadShedder(max_tokens=100, refill_rate=50.0)
        try:
            with shedder:
                handle_request()
        except LoadShedder.Rejected:
            return Response(status=503)
    """

    class Rejected(Exception):
        """Raised when a request is shed."""
        pass

    def __init__(
        self,
        max_tokens: float = 100.0,
        refill_rate: float = 50.0,
        degrade_threshold: float = 0.2,
        cost_per_request: float = 1.0,
    ) -> None:
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.degrade_threshold = degrade_threshold
        self.cost_per_request = cost_per_request

        self._tokens = max_tokens
        self._last_refill = time.monotonic()
        self._lock = Lock()

        self._total_requests = 0
        self._admitted = 0
        self._shed = 0
        self._degraded = 0

    def _refill(self) -> None:
        """Add tokens based on elapsed time (must be called under lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def check(self, cost: float = 0.0) -> ShedDecision:
        """Check if a request should be admitted, degraded, or shed.

        Consumes tokens on admit/degrade. Uses ``cost`` if provided,
        otherwise ``cost_per_request``.
        """
        actual_cost = cost if cost > 0 else self.cost_per_request
        with self._lock:
            self._refill()
            self._total_requests += 1

            if self._tokens < actual_cost:
                self._shed += 1
                return ShedDecision.SHED

            self._tokens -= actual_cost
            threshold = self.max_tokens * self.degrade_threshold

            if self._tokens < threshold:
                self._degraded += 1
                return ShedDecision.DEGRADED

            self._admitted += 1
            return ShedDecision.ADMIT

    def try_acquire(self, cost: float = 0.0) -> bool:
        """Simple boolean check: True if request is allowed (admit or degraded)."""
        decision = self.check(cost)
        return decision != ShedDecision.SHED

    @property
    def available_tokens(self) -> float:
        """Current token count (after refill)."""
        with self._lock:
            self._refill()
            return self._tokens

    @property
    def utilization(self) -> float:
        """Fraction of tokens consumed (0.0 = idle, 1.0 = fully loaded)."""
        with self._lock:
            self._refill()
            return 1.0 - (self._tokens / self.max_tokens)

    def get_stats(self) -> ShedStats:
        """Return current load shedding statistics."""
        with self._lock:
            self._refill()
            return ShedStats(
                total_requests=self._total_requests,
                admitted=self._admitted,
                shed=self._shed,
                degraded=self._degraded,
                current_tokens=self._tokens,
                max_tokens=self.max_tokens,
                refill_rate=self.refill_rate,
            )

    def reset(self) -> None:
        """Reset tokens to max and clear statistics."""
        with self._lock:
            self._tokens = self.max_tokens
            self._last_refill = time.monotonic()
            self._total_requests = 0
            self._admitted = 0
            self._shed = 0
            self._degraded = 0

    def __enter__(self) -> ShedDecision:
        decision = self.check()
        if decision == ShedDecision.SHED:
            raise LoadShedder.Rejected("load shedder: request rejected (503)")
        return decision

    def __exit__(self, *args) -> None:
        pass
