"""Self-healing metric collectors.

Wraps collection functions with automatic failure detection,
restart, and recovery logging. Zero external dependencies.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class CollectorStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"
    RECOVERING = "recovering"
    STOPPED = "stopped"


@dataclass
class CollectorRecord:
    name: str
    status: CollectorStatus = CollectorStatus.IDLE
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_failure_error: str = ""
    recovery_count: int = 0
    last_run_time: float = 0.0
    total_runs: int = 0


class SelfHealingCollector:

    def __init__(self, max_retries=3, base_backoff=1.0, max_backoff=60.0):
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._collectors: Dict[str, Callable[[], Any]] = {}
        self._records: Dict[str, CollectorRecord] = {}
        self._lock = threading.Lock()

    def register(self, name, fn):
        with self._lock:
            self._collectors[name] = fn
            self._records[name] = CollectorRecord(name=name)

    def unregister(self, name):
        with self._lock:
            if name in self._collectors:
                del self._collectors[name]
                del self._records[name]
                return True
            return False

    def run(self, name):
        with self._lock:
            fn = self._collectors.get(name)
            record = self._records.get(name)
        if fn is None or record is None:
            return None
        for attempt in range(self.max_retries + 1):
            record.status = CollectorStatus.RUNNING
            try:
                result = fn()
                record.last_run_time = time.time()
                record.total_runs += 1
                record.failure_count = 0
                return result
            except Exception as exc:
                record.failure_count += 1
                record.last_failure_time = time.time()
                record.last_failure_error = str(exc)
                record.status = CollectorStatus.FAILED
                logger.warning("Collector %r failed (attempt %d/%d): %s",
                               name, attempt + 1, self.max_retries + 1, exc)
                if attempt < self.max_retries:
                    record.status = CollectorStatus.RECOVERING
                    record.recovery_count += 1
                    backoff = min(self.base_backoff * (2 ** attempt), self.max_backoff)
                    time.sleep(backoff)
        record.status = CollectorStatus.FAILED
        return None

    def run_all(self):
        with self._lock:
            names = list(self._collectors.keys())
        return {name: self.run(name) for name in names}

    def get_status(self, name):
        with self._lock:
            record = self._records.get(name)
        if record is None:
            return None
        return {
            "name": record.name, "status": record.status.value,
            "failure_count": record.failure_count, "recovery_count": record.recovery_count,
            "total_runs": record.total_runs, "last_failure_error": record.last_failure_error,
        }

    def get_all_status(self):
        with self._lock:
            names = list(self._records.keys())
        return {name: self.get_status(name) for name in names}

    @property
    def collector_names(self):
        with self._lock:
            return list(self._collectors.keys())

    @property
    def healthy_count(self):
        with self._lock:
            return sum(1 for r in self._records.values() if r.status != CollectorStatus.FAILED)

    @property
    def failed_count(self):
        with self._lock:
            return sum(1 for r in self._records.values() if r.status == CollectorStatus.FAILED)

    def reset(self, name):
        with self._lock:
            record = self._records.get(name)
            if record is None:
                return False
            record.status = CollectorStatus.IDLE
            record.failure_count = 0
            record.last_failure_error = ""
            return True

    def reset_all(self):
        with self._lock:
            for record in self._records.values():
                record.status = CollectorStatus.IDLE
                record.failure_count = 0
                record.last_failure_error = ""
