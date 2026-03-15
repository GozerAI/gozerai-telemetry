"""Span pooling to reduce allocation overhead in high-throughput tracing.

Pre-allocates a pool of Span objects that are reused instead of creating
new instances for each traced operation.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional

from gozerai_telemetry.tracing import _current_span


@dataclass
class PooledSpan:
    """A reusable span that can be returned to a pool after use."""

    name: str = ""
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: Optional[str] = None
    service_name: str = ""
    start_time: float = 0.0
    end_time: Optional[float] = None
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)
    _pool: Optional["SpanPool"] = field(default=None, repr=False)
    _in_use: bool = field(default=False, repr=False)

    def reset(
        self,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        service_name: str,
    ) -> None:
        """Reset the span for reuse with new operation data."""
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.service_name = service_name
        self.start_time = time.time()
        self.end_time = None
        self.status = "ok"
        self.attributes.clear()
        self._in_use = True

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

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

    def release(self) -> None:
        """Return this span to its pool."""
        if self._pool is not None:
            self._pool.release(self)

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
            "attributes": dict(self.attributes),
        }


class SpanPool:
    """Pre-allocated pool of PooledSpan objects for reuse.

    Usage:
        pool = SpanPool("myservice", pool_size=50)
        with pool.span("operation") as s:
            s.set_attribute("key", "value")
        # span is automatically returned to the pool
    """

    def __init__(self, service_name: str, pool_size: int = 100) -> None:
        self.service_name = service_name
        self.pool_size = pool_size
        self._available: deque[PooledSpan] = deque()
        self._lock = Lock()
        self._total_acquired = 0
        self._total_created = 0
        self._pool_misses = 0
        self._completed: List[Dict[str, Any]] = []
        self._max_completed = 1000

        # Pre-allocate spans
        for _ in range(pool_size):
            s = PooledSpan(_pool=self)
            self._available.append(s)
            self._total_created += 1

    def acquire(
        self,
        name: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
    ) -> PooledSpan:
        """Acquire a span from the pool, or create a new one if empty."""
        span_id = uuid.uuid4().hex[:16]
        if trace_id is None:
            parent = _current_span.get()
            if parent is not None:
                trace_id = parent.trace_id
                if hasattr(parent, "span_id"):
                    parent_span_id = parent.span_id
            else:
                trace_id = uuid.uuid4().hex

        with self._lock:
            self._total_acquired += 1
            if self._available:
                s = self._available.popleft()
            else:
                self._pool_misses += 1
                self._total_created += 1
                s = PooledSpan(_pool=self)

        s.reset(name, trace_id, span_id, parent_span_id, self.service_name)
        return s

    def release(self, span: PooledSpan) -> None:
        """Return a span to the pool."""
        if span._in_use:
            with self._lock:
                self._completed.append(span.to_dict())
                if len(self._completed) > self._max_completed:
                    self._completed = self._completed[-self._max_completed:]

            span._in_use = False
            with self._lock:
                if len(self._available) < self.pool_size:
                    self._available.append(span)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[PooledSpan]:
        """Context manager that acquires a span, uses it, and releases it."""
        s = self.acquire(name)
        for k, v in attributes.items():
            s.set_attribute(k, v)

        token = _current_span.set(s)
        try:
            yield s
        except Exception as e:
            s.set_error(e)
            raise
        finally:
            s.end()
            _current_span.reset(token)
            s.release()

    def get_completed(self) -> List[Dict[str, Any]]:
        """Get completed span data (copies, since spans may be reused)."""
        with self._lock:
            return list(self._completed)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "service": self.service_name,
                "pool_size": self.pool_size,
                "available": len(self._available),
                "total_acquired": self._total_acquired,
                "total_created": self._total_created,
                "pool_misses": self._pool_misses,
                "completed_spans": len(self._completed),
            }

    def clear(self) -> None:
        """Clear completed span history."""
        with self._lock:
            self._completed.clear()
