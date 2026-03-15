"""Metric export buffering with configurable flush intervals.

Collects metric snapshots in a buffer and flushes them to registered
export handlers (callbacks) at configurable intervals.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from gozerai_telemetry.metrics import MetricsCollector


class ExportBuffer:
    """Buffers metric snapshots and flushes to export handlers.

    Usage:
        buffer = ExportBuffer(max_size=1000, flush_interval=5.0)
        buffer.add_handler(lambda snapshots: print(f"Exported {len(snapshots)}"))
        buffer.record({"metric": "value"})
        buffer.flush()
    """

    def __init__(self, max_size: int = 1000, flush_interval: float = 5.0) -> None:
        self.max_size = max_size
        self.flush_interval = flush_interval
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._handlers: List[Callable[[List[Dict[str, Any]]], None]] = []
        self._total_flushed = 0
        self._total_dropped = 0
        self._last_flush_time = time.monotonic()

    def add_handler(self, handler: Callable[[List[Dict[str, Any]]], None]) -> None:
        """Register an export handler that receives batches of snapshots."""
        self._handlers.append(handler)

    def record(self, snapshot: Dict[str, Any]) -> bool:
        """Add a metric snapshot to the buffer. Returns False if dropped."""
        flush_needed = False
        with self._lock:
            if len(self._buffer) >= self.max_size:
                self._total_dropped += 1
                return False
            self._buffer.append(snapshot)
            now = time.monotonic()
            if (
                len(self._buffer) >= self.max_size
                or (now - self._last_flush_time) >= self.flush_interval
            ):
                flush_needed = True

        if flush_needed:
            self.flush()
        return True

    def flush(self) -> int:
        """Flush buffered snapshots to all handlers. Returns count flushed."""
        with self._lock:
            to_flush = self._buffer
            self._buffer = []
            self._last_flush_time = time.monotonic()

        if not to_flush:
            return 0

        for handler in self._handlers:
            try:
                handler(to_flush)
            except Exception:
                pass

        self._total_flushed += len(to_flush)
        return len(to_flush)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "buffered": len(self._buffer),
                "max_size": self.max_size,
                "total_flushed": self._total_flushed,
                "total_dropped": self._total_dropped,
                "handler_count": len(self._handlers),
                "flush_interval": self.flush_interval,
            }


class BufferedExporter:
    """Automatically captures metrics from a collector at regular intervals.

    Runs a background thread that periodically snapshots metrics and
    flushes them through the ExportBuffer.

    Usage:
        collector = get_collector("myapp")
        exporter = BufferedExporter(collector, flush_interval=10.0)
        exporter.add_handler(send_to_monitoring)
        exporter.start()
        ...
        exporter.stop()
    """

    def __init__(
        self,
        collector: MetricsCollector,
        flush_interval: float = 10.0,
        buffer_size: int = 1000,
    ) -> None:
        self.collector = collector
        self._buffer = ExportBuffer(max_size=buffer_size, flush_interval=flush_interval)
        self._interval = flush_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot_count = 0

    def add_handler(self, handler: Callable[[List[Dict[str, Any]]], None]) -> None:
        self._buffer.add_handler(handler)

    def start(self) -> None:
        """Start the background export thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="buffered-exporter")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background export thread and flush remaining data."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._buffer.flush()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if not self._stop_event.is_set():
                snapshot = self.collector.to_dict()
                snapshot["_snapshot_time"] = time.time()
                self._buffer.record(snapshot)
                self._snapshot_count += 1
                self._buffer.flush()

    def get_stats(self) -> Dict[str, Any]:
        stats = self._buffer.get_stats()
        stats["running"] = self.is_running
        stats["snapshot_count"] = self._snapshot_count
        return stats
