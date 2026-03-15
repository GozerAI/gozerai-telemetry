"""Offline metric storage with disk buffering and sync-on-reconnect.

Buffers metrics to a local file when the remote backend is unavailable.
On reconnect, replays buffered data through a sync callback.
JSON-lines format. Zero external dependencies.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional


class SyncStatus(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NO_DATA = "no_data"


@dataclass
class StorageEntry:
    name: str
    value: float
    entry_type: str = "metric"
    labels: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "value": self.value,
            "entry_type": self.entry_type, "labels": self.labels,
            "metadata": self.metadata, "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data["name"], value=data["value"],
            entry_type=data.get("entry_type", "metric"),
            labels=data.get("labels", {}),
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class SyncResult:
    status: SyncStatus = SyncStatus.NO_DATA
    total: int = 0
    synced: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value, "total": self.total,
            "synced": self.synced, "failed": self.failed,
            "errors": self.errors[:10],
        }


class OfflineStorage:

    def __init__(self, storage_dir="", max_file_size_bytes=10485760,
                 max_entries=100_000, batch_size=500):
        if storage_dir:
            self._storage_dir = Path(storage_dir)
        else:
            self._storage_dir = Path.home() / ".gozerai_telemetry" / "offline"
        self._max_file_size = max_file_size_bytes
        self._max_entries = max_entries
        self._batch_size = batch_size
        self._lock = Lock()
        self._buffer: List[StorageEntry] = []
        self._total_stored = 0
        self._total_synced = 0
        self._total_dropped = 0
        self._is_online = True

    def store(self, entry):
        with self._lock:
            if len(self._buffer) >= self._max_entries:
                self._total_dropped += 1
                return False
            self._buffer.append(entry)
            self._total_stored += 1
            return True

    def store_metric(self, name, value, **labels):
        entry = StorageEntry(name=name, value=value, labels=labels)
        return self.store(entry)

    def store_to_disk(self):
        with self._lock:
            to_write = list(self._buffer)
            self._buffer.clear()
        if not to_write:
            return 0
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        file_path = self._storage_dir / f"buffer_{ts}.jsonl"
        written = 0
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for entry in to_write:
                    f.write(json.dumps(entry.to_dict()) + chr(10))
                    written += 1
        except OSError:
            with self._lock:
                self._buffer.extend(to_write[written:])
        return written

    def load_from_disk(self):
        entries = []
        if not self._storage_dir.exists():
            return entries
        for fp in sorted(self._storage_dir.glob("buffer_*.jsonl")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(StorageEntry.from_dict(json.loads(line)))
            except (OSError, json.JSONDecodeError):
                continue
        return entries

    def sync(self, sync_fn, include_disk=True):
        with self._lock:
            memory_entries = list(self._buffer)
        disk_entries = self.load_from_disk() if include_disk else []
        all_entries = disk_entries + memory_entries
        if not all_entries:
            return SyncResult(status=SyncStatus.NO_DATA)
        result = SyncResult(total=len(all_entries))
        offset = 0
        while offset < len(all_entries):
            batch = all_entries[offset:offset + self._batch_size]
            try:
                ok = sync_fn(batch)
                if ok:
                    result.synced += len(batch)
                else:
                    result.failed += len(batch)
                    result.errors.append(f"sync_fn returned False at offset {offset}")
            except Exception as exc:
                result.failed += len(batch)
                result.errors.append(str(exc))
            offset += self._batch_size
        if result.failed == 0:
            result.status = SyncStatus.SUCCESS
            with self._lock:
                self._buffer.clear()
            self._total_synced += result.synced
            if include_disk:
                self._clear_disk_files()
        elif result.synced > 0:
            result.status = SyncStatus.PARTIAL
            self._total_synced += result.synced
        else:
            result.status = SyncStatus.FAILED
        return result

    def _clear_disk_files(self):
        cleared = 0
        if not self._storage_dir.exists():
            return 0
        for fp in self._storage_dir.glob("buffer_*.jsonl"):
            try:
                fp.unlink()
                cleared += 1
            except OSError:
                pass
        return cleared

    def set_online(self, online=True):
        self._is_online = online

    @property
    def is_online(self):
        return self._is_online

    @property
    def pending_count(self):
        with self._lock:
            return len(self._buffer)

    @property
    def disk_file_count(self):
        if not self._storage_dir.exists():
            return 0
        return len(list(self._storage_dir.glob("buffer_*.jsonl")))

    def get_stats(self):
        with self._lock:
            buffered = len(self._buffer)
        return {
            "buffered": buffered, "disk_files": self.disk_file_count,
            "total_stored": self._total_stored, "total_synced": self._total_synced,
            "total_dropped": self._total_dropped, "max_entries": self._max_entries,
            "is_online": self._is_online, "storage_dir": str(self._storage_dir),
        }

    def clear(self):
        with self._lock:
            self._buffer.clear()
        self._clear_disk_files()
