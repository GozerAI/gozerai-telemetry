# GozerAI Telemetry

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lightweight telemetry library for metrics, tracing, and health checks. Zero runtime dependencies. Outputs Prometheus-compatible metrics format.

Part of the [GozerAI](https://gozerai.com) ecosystem.

## Features

- **Metrics** — Counter, Gauge, and Histogram with Prometheus text format export
- **Tracing** — Span-based distributed tracing with context propagation via `contextvars`
- **Health** — Health check registration and reporting
- **Resilience** — Circuit breaker and retry utilities
- **Zero dependencies** — Pure Python, no external packages required

## Installation

```bash
pip install -e .

# With dev tools
pip install -e ".[dev]"
```

## Metrics

```python
from gozerai_telemetry.metrics import MetricsCollector

# Get a service-scoped collector (singleton per service name)
metrics = MetricsCollector.get_collector("my-service")

# Counter — monotonically increasing value
requests = metrics.counter("http_requests_total", "Total HTTP requests")
requests.inc()
requests.inc(labels={"method": "GET", "status": "200"})

# Gauge — value that can go up and down
active = metrics.gauge("active_connections", "Current active connections")
active.set(42)
active.inc()
active.dec()

# Histogram — observe value distributions
latency = metrics.histogram(
    "request_duration_seconds",
    "Request duration",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)
latency.observe(0.127)

# Export in Prometheus text format
print(metrics.render())
```

Output:

```
# HELP my_service_http_requests_total Total HTTP requests
# TYPE my_service_http_requests_total counter
my_service_http_requests_total 1
my_service_http_requests_total{method="GET",status="200"} 1
# HELP my_service_active_connections Current active connections
# TYPE my_service_active_connections gauge
my_service_active_connections 43
...
```

## Tracing

```python
from gozerai_telemetry.tracing import Tracer

tracer = Tracer("my-service")

# Create a span (automatically tracks parent via contextvars)
with tracer.span("handle_request") as span:
    span.set_attribute("http.method", "GET")
    span.set_attribute("http.url", "/api/data")

    with tracer.span("db_query") as child:
        child.set_attribute("db.statement", "SELECT ...")
        # child automatically has handle_request as parent

    span.set_status("ok")

# Access completed spans
for s in tracer.spans:
    print(f"{s.name}: {s.duration_ms:.1f}ms")
```

## Health Checks

```python
from gozerai_telemetry.health import HealthReporter

reporter = HealthReporter("my-service")

# Register health checks
reporter.register("database", lambda: {"status": "up", "latency_ms": 5})
reporter.register("cache", lambda: {"status": "up", "hit_rate": 0.92})

# Run all checks
report = reporter.check()
print(report.status)       # "healthy" or "degraded" or "unhealthy"
print(report.checks)       # Individual check results
print(report.to_dict())    # JSON-serializable output
```

## Resilience

```python
from gozerai_telemetry.resilience import CircuitBreaker

breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30.0
)

# Wrap calls with circuit breaker protection
try:
    result = breaker.call(some_external_service)
except CircuitBreakerOpen:
    # Circuit is open, use fallback
    result = cached_value
```

## API Summary

| Module | Classes | Description |
|--------|---------|-------------|
| `metrics` | `MetricsCollector`, `Counter`, `Gauge`, `Histogram` | Prometheus-format metrics |
| `tracing` | `Tracer`, `Span` | Distributed tracing with contextvars |
| `health` | `HealthReporter` | Health check registration and reporting |
| `resilience` | `CircuitBreaker` | Circuit breaker pattern |

## License

MIT — see [LICENSE](LICENSE) for details. Learn more at [gozerai.com](https://gozerai.com).
