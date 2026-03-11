"""Distributed tracing compatible with C-Suite's observability/tracing.py.

Provides lightweight span tracking with context propagation.
Zero dependencies — uses contextvars for async-safe context.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

# Active span context propagation
_current_span: ContextVar[Optional["Span"]] = ContextVar("_current_span", default=None)


@dataclass
class SpanEvent:
    """An event recorded during a span."""

    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """A single traced operation."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    service_name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, **attributes: Any) -> None:
        self.events.append(SpanEvent(name=name, attributes=attributes))

    def set_error(self, error: Exception) -> None:
        self.status = "error"
        self.attributes["error.type"] = type(error).__name__
        self.attributes["error.message"] = str(error)

    def end(self) -> None:
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "service": self.service_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "events": [{"name": e.name, "time": e.timestamp, "attrs": e.attributes} for e in self.events],
        }


class Tracer:
    """Creates and manages spans for a service."""

    def __init__(self, service_name: str, max_spans: int = 1000) -> None:
        self.service_name = service_name
        self._max_spans = max_spans
        self._completed: List[Span] = []

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        """Context manager that creates a span, sets it as current, and records it."""
        parent = _current_span.get()
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        parent_span_id = parent.span_id if parent else None

        s = Span(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            service_name=self.service_name,
            attributes=dict(attributes),
        )

        token = _current_span.set(s)
        try:
            yield s
        except Exception as e:
            s.set_error(e)
            raise
        finally:
            s.end()
            _current_span.reset(token)
            self._record(s)

    def _record(self, s: Span) -> None:
        self._completed.append(s)
        if len(self._completed) > self._max_spans:
            self._completed = self._completed[-self._max_spans:]

    def get_completed(self) -> List[Span]:
        return list(self._completed)

    def clear(self) -> None:
        self._completed.clear()

    def get_traces(self) -> Dict[str, List[Span]]:
        """Group completed spans by trace_id."""
        traces: Dict[str, List[Span]] = {}
        for s in self._completed:
            traces.setdefault(s.trace_id, []).append(s)
        return traces


@contextmanager
def span(name: str, service: str = "unknown", **attributes: Any) -> Iterator[Span]:
    """Convenience standalone span context manager."""
    tracer = Tracer(service)
    with tracer.span(name, **attributes) as s:
        yield s
