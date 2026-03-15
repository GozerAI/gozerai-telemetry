"""Self-sufficiency modules for autonomous telemetry operation.

Provides offline storage with sync-on-reconnect, text-based dashboards,
automatic metric documentation, self-healing collectors, and multi-format
metric export -- all with zero external dependencies.
"""

from gozerai_telemetry.self_sufficiency.offline_storage import (
    OfflineStorage,
    StorageEntry,
    SyncResult,
    SyncStatus,
)
from gozerai_telemetry.self_sufficiency.text_dashboard import TextDashboard, DashboardPanel
from gozerai_telemetry.self_sufficiency.metric_docs import MetricDocGenerator, MetricDoc
from gozerai_telemetry.self_sufficiency.self_healing import SelfHealingCollector, CollectorStatus
from gozerai_telemetry.self_sufficiency.export_formats import MetricExporter, ExportFormat

__all__ = [
    "OfflineStorage", "StorageEntry", "SyncResult", "SyncStatus",
    "TextDashboard", "DashboardPanel",
    "MetricDocGenerator", "MetricDoc",
    "SelfHealingCollector", "CollectorStatus",
    "MetricExporter", "ExportFormat",
]
