"""Offline metric buffering for self-sufficient telemetry.

Buffers metric observations, health check results, and trace spans
locally when the export endpoint is unreachable. Provides replay
capabilities when connectivity is restored.

Zero external dependencies.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple


class BufferEntryType(Enum):
    METRIC = "metric"
    HEALTH = "health"
    TRACE = "trace"


class FlushStatus(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class BufferedEntry:
    """A single buffered telemetry entry."""

    entry_type: BufferEntryType
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    flushed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.entry_type.value,
            "name": self.name,
            "value": self.value,
            "labels": self.labels,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "flushed": self.flushed,
        }


@dataclass
class FlushResult:
    """Result of a buffer flush operation."""

    status: FlushStatus = FlushStatus.SUCCESS
    total: int = 0
    flushed: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "total": self.total,
            "flushed": self.flushed,
            "failed": self.failed,
            "errors": self.errors[:10],
        }


class OfflineMetricBuffer:
    """Thread-safe offline buffer for telemetry data.

    Stores metric observations, health results, and trace data when the
    export backend is unavailable. Supports batch flushing via a callback.

    Usage:
        buffer = OfflineMetricBuffer(max_size=10000)

        # Buffer metrics when offline
        buffer.record_metric("http_requests_total", 1.0, method="GET")
        buffer.record_health("database", 1.0)  # 1.0 = healthy
        buffer.record_trace("collect_trends", 0.042)

        # Flush when online
        async def export(entries):
            for e in entries:
                await send_to_backend(e)
            return True

        result = buffer.flush_sync(export_fn)
    """

    def __init__(
        self,
        max_size: int = 50_000,
        max_age_seconds: float = 86_400.0,
        batch_size: int = 500,
    ) -> None:
        self._max_size = max_size
        self._max_age = max_age_seconds
        self._batch_size = batch_size
        self._buffer: Deque[BufferedEntry] = deque(maxlen=max_size)
        self._lock = Lock()
        self._total_buffered = 0
        self._total_flushed = 0
        self._total_dropped = 0
        self._total_expired = 0
        self._is_online = True

    # -- Recording ---------------------------------------------------

    def record_metric(
        self, name: str, value: float, **labels: str,
    ) -> BufferedEntry:
        """Buffer a metric observation."""
        entry = BufferedEntry(
            entry_type=BufferEntryType.METRIC,
            name=name,
            value=value,
            labels=labels,
        )
        return self._append(entry)

    def record_health(
        self, check_name: str, value: float, **metadata: Any,
    ) -> BufferedEntry:
        """Buffer a health check result (1.0=healthy, 0.0=unhealthy)."""
        entry = BufferedEntry(
            entry_type=BufferEntryType.HEALTH,
            name=check_name,
            value=value,
            metadata=metadata,
        )
        return self._append(entry)

    def record_trace(
        self, span_name: str, duration_seconds: float, **metadata: Any,
    ) -> BufferedEntry:
        """Buffer a trace span."""
        entry = BufferedEntry(
            entry_type=BufferEntryType.TRACE,
            name=span_name,
            value=duration_seconds,
            metadata=metadata,
        )
        return self._append(entry)

    # -- Flushing ----------------------------------------------------

    def flush_sync(
        self,
        export_fn: Callable[[List[BufferedEntry]], bool],
    ) -> FlushResult:
        """Flush buffered entries via a synchronous export function.

        export_fn receives a batch of entries and returns True on success.
        Entries are removed from the buffer only on success.
        """
        with self._lock:
            pending = [e for e in self._buffer if not e.flushed]
            batch = pending[: self._batch_size]

        if not batch:
            return FlushResult(status=FlushStatus.SUCCESS)

        result = FlushResult(total=len(batch))
        try:
            ok = export_fn(batch)
            if ok:
                for entry in batch:
                    entry.flushed = True
                result.flushed = len(batch)
                result.status = FlushStatus.SUCCESS
                self._total_flushed += len(batch)
                self._purge_flushed()
            else:
                result.failed = len(batch)
                result.status = FlushStatus.FAILED
                result.errors.append("export_fn returned False")
        except Exception as exc:
            result.failed = len(batch)
            result.status = FlushStatus.FAILED
            result.errors.append(str(exc))

        return result

    # -- State management -------------------------------------------

    def set_online(self, online: bool = True) -> None:
        """Set the online/offline status."""
        self._is_online = online

    @property
    def is_online(self) -> bool:
        return self._is_online

    def pending_count(self) -> int:
        """Count of entries awaiting flush."""
        with self._lock:
            return sum(1 for e in self._buffer if not e.flushed)

    def expire_old(self) -> int:
        """Remove entries older than max_age_seconds."""
        cutoff = time.time() - self._max_age
        with self._lock:
            before = len(self._buffer)
            self._buffer = deque(
                (e for e in self._buffer if e.timestamp >= cutoff),
                maxlen=self._max_size,
            )
            expired = before - len(self._buffer)
            self._total_expired += expired
            return expired

    def clear(self) -> None:
        """Drop all buffered entries."""
        with self._lock:
            self._buffer.clear()

    def get_entries(
        self,
        entry_type: Optional[BufferEntryType] = None,
        limit: int = 100,
    ) -> List[BufferedEntry]:
        """Retrieve buffered entries, optionally filtered by type."""
        with self._lock:
            entries = list(self._buffer)
        if entry_type is not None:
            entries = [e for e in entries if e.entry_type == entry_type]
        return entries[:limit]

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            size = len(self._buffer)
            pending = sum(1 for e in self._buffer if not e.flushed)
        return {
            "buffer_size": size,
            "pending": pending,
            "max_size": self._max_size,
            "total_buffered": self._total_buffered,
            "total_flushed": self._total_flushed,
            "total_dropped": self._total_dropped,
            "total_expired": self._total_expired,
            "is_online": self._is_online,
        }

    # -- Internal ----------------------------------------------------

    def _append(self, entry: BufferedEntry) -> BufferedEntry:
        with self._lock:
            was_full = len(self._buffer) == self._max_size
            self._buffer.append(entry)
            self._total_buffered += 1
            if was_full:
                self._total_dropped += 1
        return entry

    def _purge_flushed(self) -> int:
        with self._lock:
            before = len(self._buffer)
            self._buffer = deque(
                (e for e in self._buffer if not e.flushed),
                maxlen=self._max_size,
            )
            return before - len(self._buffer)
