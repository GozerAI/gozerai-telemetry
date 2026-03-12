"""GozerAI Telemetry — lightweight observability for standalone products.

Zero dependencies. Lightweight observability.
Import and use in any GozerAI product to emit metrics, traces, and health status.

Usage:
    from gozerai_telemetry import Counter, Gauge, Histogram, get_collector
    from gozerai_telemetry import Tracer, span
    from gozerai_telemetry import HealthReporter

    # Metrics
    collector = get_collector("trendscope")
    requests = collector.counter("http_requests_total", "Total HTTP requests")
    requests.inc(method="GET", status="200")

    # Tracing
    tracer = Tracer("trendscope")
    with tracer.span("collect_trends") as s:
        s.set_attribute("source", "github")
        ...

    # Health
    health = HealthReporter("trendscope", port=9100)
    health.register_check("database", lambda: db.ping())
"""

from gozerai_telemetry.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    get_collector,
)
from gozerai_telemetry.tracing import Span, Tracer, span
from gozerai_telemetry.health import HealthCheck, HealthReporter, HealthStatus
from gozerai_telemetry.resilience import (
    RetryPolicy,
    CircuitBreaker,
    CircuitState,
    resilient_fetch,
    resilient_request,
    get_circuit_breaker,
    reset_all_breakers,
    DEFAULT_RETRY,
    CONSERVATIVE_RETRY,
    AGGRESSIVE_RETRY,
)

__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsCollector",
    "get_collector",
    "Span",
    "Tracer",
    "span",
    "HealthCheck",
    "HealthReporter",
    "HealthStatus",
    "RetryPolicy",
    "CircuitBreaker",
    "CircuitState",
    "resilient_fetch",
    "resilient_request",
    "get_circuit_breaker",
    "reset_all_breakers",
    "DEFAULT_RETRY",
    "CONSERVATIVE_RETRY",
    "AGGRESSIVE_RETRY",
]
