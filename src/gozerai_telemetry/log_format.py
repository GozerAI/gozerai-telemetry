"""Structured JSON logging for consistent output across all GozerAI services.

Zero dependencies — uses only stdlib logging and json.
Integrates with the tracing module to include trace/span IDs when available.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class StructuredFormatter(logging.Formatter):
    """Formats log records as JSON lines for structured log aggregation.

    Each line is a valid JSON object containing:
      - timestamp (ISO8601 with timezone)
      - level (log level name)
      - service (service name)
      - message (the log message)
      - logger (logger name)
      - trace_id / span_id (if present in the active tracing context)
      - Any extra fields attached to the log record
    """

    # Fields that belong to the standard LogRecord and should not be
    # forwarded as extra fields.
    _RESERVED = frozenset({
        "name", "msg", "args", "created", "relativeCreated",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "filename", "module", "pathname", "thread", "threadName",
        "process", "processName", "levelname", "levelno", "msecs",
        "message", "taskName",
    })

    def __init__(self, service_name: str = "unknown", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Build the base payload
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject trace context if the tracing module has an active span
        trace_id, span_id = _get_trace_context()
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        # Inject correlation ID if set
        correlation_id = _get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        # Collect extra fields from the log record
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._RESERVED:
                continue
            # Skip internal formatter fields
            if key in ("service_name",):
                continue
            payload[key] = value

        # Format exception info
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(
    service_name: str,
    level: str = "INFO",
    stream: Any = None,
) -> logging.Logger:
    """Configure the root logger with a StructuredFormatter.

    Args:
        service_name: Name of the service (appears in every log line).
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        stream: Optional stream for the handler (defaults to stderr).

    Returns:
        The configured root logger.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter(service_name=service_name))
    root.addHandler(handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    The logger inherits the root configuration set by ``setup_logging``.
    """
    return logging.getLogger(name)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _get_trace_context() -> tuple[Optional[str], Optional[str]]:
    """Read trace_id/span_id from the tracing module's active span."""
    try:
        from gozerai_telemetry.tracing import _current_span
        span = _current_span.get()
        if span is not None:
            return span.trace_id, span.span_id
    except Exception:
        pass
    return None, None


def _get_correlation_id() -> Optional[str]:
    """Read correlation ID from the correlation module if available."""
    try:
        from gozerai_telemetry.correlation import get_correlation_id
        return get_correlation_id()
    except Exception:
        return None
