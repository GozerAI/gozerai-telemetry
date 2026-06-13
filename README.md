# Gozerai Telemetry

**Lightweight, zero-dependency observability for Python services** — part of the [GozerAI](https://gozerai.com) ecosystem.

A single small package that emits Prometheus-compatible metrics, distributed traces, health status, structured logs, and SLO tracking — with no third-party dependencies. Drop it into any service to get consistent telemetry across a fleet.

## Features

- **Metrics** — `Counter`, `Gauge`, `Histogram` with a Prometheus text-exposition collector
- **Tracing** — lightweight spans with attributes and correlation propagation
- **Health** — a `HealthReporter` with pluggable checks and timeouts
- **Structured logging** — JSON log formatting with correlation IDs
- **Correlation** — request-scoped correlation context + header injection/extraction
- **Resilience patterns** — rate limiting, retries, and circuit-breaker helpers
- **SLO** — service-level-objective definitions and burn-rate tracking

## Installation

```bash
pip install -e .
```

No runtime dependencies (Python >= 3.10).

## Usage

```python
from gozerai_telemetry import get_collector, Tracer, HealthReporter, setup_logging, get_logger

# Metrics
collector = get_collector("my-service")
requests = collector.counter("http_requests_total", "Total HTTP requests")
requests.inc(method="GET", status="200")

# Tracing
tracer = Tracer("my-service")
with tracer.span("handle_request") as s:
    s.set_attribute("route", "/health")

# Health
health = HealthReporter("my-service", port=9100)
health.register_check("database", lambda: db.ping())

# Structured logging
setup_logging("my-service", level="INFO")
logger = get_logger("my-service.api")
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE). Part of the GozerAI ecosystem; see [gozerai.com/pricing](https://gozerai.com/pricing) for the commercial product tiers that build on it.
