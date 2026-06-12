"""Health reporting for standalone products.

Standardizes health checks across all GozerAI services so they can be
aggregated by a central collector or Kubernetes probes.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheck:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    message: str = ""
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class HealthReporter:
    """Aggregates health checks for a service.

    Usage:
        reporter = HealthReporter("trendscope")
        reporter.register_check("database", lambda: db.ping())
        reporter.register_check("redis", lambda: redis.ping())

        report = reporter.check_all()
        # {"service": "trendscope", "status": "healthy", "checks": [...]}
    """

    def __init__(self, service_name: str, version: str = "0.0.0") -> None:
        self.service_name = service_name
        self.version = version
        self._checks: Dict[str, Tuple[Callable[[], bool], float, bool]] = {}
        self._started_at = time.time()

    def register_check(
        self,
        name: str,
        check_fn: Callable[[], bool],
        timeout: float = 5.0,
        critical: bool = True,
    ) -> None:
        """Register a health check function.

        Args:
            name: Unique name for the check.
            check_fn: Callable returning True if healthy.
            timeout: Maximum seconds to wait before marking as timed out.
            critical: If True, failure sets overall status to UNHEALTHY.
                      If False, failure sets overall status to DEGRADED.
        """
        self._checks[name] = (check_fn, timeout, critical)

    def unregister_check(self, name: str) -> None:
        self._checks.pop(name, None)

    def check_all(self) -> Dict[str, Any]:
        """Run all health checks and return aggregated report."""
        results: List[HealthCheck] = []
        overall = HealthStatus.HEALTHY

        for name, (check_fn, timeout, critical) in self._checks.items():
            start = time.monotonic()
            fail_status = HealthStatus.UNHEALTHY if critical else HealthStatus.DEGRADED
            try:
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(check_fn)
                try:
                    ok = future.result(timeout=timeout)
                except FuturesTimeoutError:
                    duration = (time.monotonic() - start) * 1000
                    results.append(HealthCheck(
                        name=name,
                        status=fail_status,
                        message="Health check timed out",
                        duration_ms=duration,
                    ))
                    # Shut down without waiting for the hung thread
                    executor.shutdown(wait=False)
                    continue
                executor.shutdown(wait=False)
                duration = (time.monotonic() - start) * 1000
                status = HealthStatus.HEALTHY if ok else fail_status
                results.append(HealthCheck(name=name, status=status, duration_ms=duration))
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                results.append(HealthCheck(
                    name=name,
                    status=fail_status,
                    message=str(e),
                    duration_ms=duration,
                ))

        # Aggregate: any unhealthy → unhealthy, any degraded → degraded
        statuses = [r.status for r in results]
        if HealthStatus.UNHEALTHY in statuses:
            overall = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall = HealthStatus.DEGRADED

        return {
            "service": self.service_name,
            "version": self.version,
            "status": overall.value,
            "uptime_seconds": time.time() - self._started_at,
            "checks": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "message": r.message,
                    "duration_ms": round(r.duration_ms, 2),
                }
                for r in results
            ],
        }

    def is_healthy(self) -> bool:
        """Quick check — True if all checks pass."""
        report = self.check_all()
        return report["status"] == "healthy"
