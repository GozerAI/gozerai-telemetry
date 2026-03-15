"""GozerAI Telemetry — lightweight observability for standalone products.

Zero dependencies. Compatible with C-Suite's observability module.
Import and use in any GozerAI product to emit metrics, traces, and health status.

Usage:
    from gozerai_telemetry import Counter, Gauge, Histogram, get_collector
    from gozerai_telemetry import Tracer, span
    from gozerai_telemetry import HealthReporter
    from gozerai_telemetry import setup_logging, get_logger
    from gozerai_telemetry import CorrelationContext, set_correlation_id, inject_headers

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

    # Structured Logging
    setup_logging("trendscope", level="INFO")
    logger = get_logger("trendscope.collector")
    logger.info("collection started", extra={"source": "github"})

    # Correlation
    with CorrelationContext("req-123"):
        headers = inject_headers({"Accept": "application/json"})
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
from gozerai_telemetry.patterns import (
    Bulkhead,
    RateLimiter,
    Timeout,
    FallbackChain,
)
from gozerai_telemetry.log_format import StructuredFormatter, setup_logging, get_logger
from gozerai_telemetry.correlation import (
    CorrelationContext,
    set_correlation_id,
    get_correlation_id,
    inject_headers,
)
from gozerai_telemetry.slo import SLI, SLO, SLOTracker

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
    "Bulkhead",
    "RateLimiter",
    "Timeout",
    "FallbackChain",
    "StructuredFormatter",
    "setup_logging",
    "get_logger",
    "CorrelationContext",
    "set_correlation_id",
    "get_correlation_id",
    "inject_headers",
    "SLI",
    "SLO",
    "SLOTracker",
]
