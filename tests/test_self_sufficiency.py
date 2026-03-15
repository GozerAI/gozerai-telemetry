"""Tests for self_sufficiency modules."""

import json
import os
import tempfile
import time
import pytest

from gozerai_telemetry.self_sufficiency import (
    OfflineStorage, StorageEntry, SyncResult, SyncStatus,
    TextDashboard, DashboardPanel,
    MetricDocGenerator, MetricDoc,
    SelfHealingCollector, CollectorStatus,
    MetricExporter, ExportFormat,
)
from gozerai_telemetry.metrics import MetricsCollector


class TestOfflineStorage:

    def test_store_and_count(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp())
        assert s.store_metric("req", 1.0, method="GET")
        assert s.pending_count == 1

    def test_store_drops_when_full(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp(), max_entries=2)
        s.store_metric("a", 1.0)
        s.store_metric("b", 2.0)
        assert s.store_metric("c", 3.0) is False

    def test_store_to_disk_and_load(self):
        d = tempfile.mkdtemp()
        s = OfflineStorage(storage_dir=d)
        s.store_metric("req", 1.0, method="GET")
        written = s.store_to_disk()
        assert written == 1
        assert s.pending_count == 0
        loaded = s.load_from_disk()
        assert len(loaded) == 1

    def test_sync_memory_only(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp())
        s.store_metric("m1", 1.0)
        received = []
        def sync_fn(entries):
            received.extend(entries)
            return True
        result = s.sync(sync_fn, include_disk=False)
        assert result.status == SyncStatus.SUCCESS
        assert result.synced == 1

    def test_sync_failure(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp())
        s.store_metric("m1", 1.0)
        result = s.sync(lambda e: False, include_disk=False)
        assert result.status == SyncStatus.FAILED

    def test_sync_no_data(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp())
        result = s.sync(lambda e: True)
        assert result.status == SyncStatus.NO_DATA

    def test_clear(self):
        d = tempfile.mkdtemp()
        s = OfflineStorage(storage_dir=d)
        s.store_metric("m1", 1.0)
        s.store_to_disk()
        s.store_metric("m2", 2.0)
        s.clear()
        assert s.pending_count == 0
        assert s.disk_file_count == 0

    def test_online_flag(self):
        s = OfflineStorage(storage_dir=tempfile.mkdtemp())
        assert s.is_online is True
        s.set_online(False)
        assert s.is_online is False

    def test_entry_serialization(self):
        e = StorageEntry(name="test", value=42.0, labels={"env": "prod"})
        d = e.to_dict()
        e2 = StorageEntry.from_dict(d)
        assert e2.name == "test"
        assert e2.labels["env"] == "prod"

    def test_sync_result_to_dict(self):
        r = SyncResult(status=SyncStatus.PARTIAL, total=10, synced=7, failed=3)
        d = r.to_dict()
        assert d["status"] == "partial"


class TestTextDashboard:

    def test_empty_dashboard(self):
        d = TextDashboard("Test")
        output = d.render()
        assert "Test" in output

    def test_add_table_panel(self):
        d = TextDashboard("Test")
        d.add_panel("Stats", lambda: {"requests": 100, "errors": 5})
        output = d.render()
        assert "100" in output

    def test_add_bar_panel(self):
        d = TextDashboard("Test")
        d.add_panel("Load", lambda: {"cpu": 75, "mem": 50}, display_type="bar")
        output = d.render()
        assert "#" in output

    def test_add_sparkline_panel(self):
        d = TextDashboard("Test")
        d.add_panel("Trend", lambda: {"t1": 10, "t2": 50, "t3": 30}, display_type="sparkline")
        output = d.render()
        assert "min=" in output

    def test_remove_panel(self):
        d = TextDashboard("Test")
        d.add_panel("P1", lambda: {})
        d.add_panel("P2", lambda: {})
        assert d.remove_panel("P1")
        assert d.panel_count == 1

    def test_panel_titles(self):
        d = TextDashboard("Test")
        d.add_panel("Alpha", lambda: {})
        d.add_panel("Beta", lambda: {})
        assert d.panel_titles == ["Alpha", "Beta"]


class TestMetricDocGenerator:

    def test_manual_register(self):
        gen = MetricDocGenerator()
        gen.register("http_requests", "counter", "Total requests", labels=["method"])
        assert gen.metric_count == 1

    def test_render_text(self):
        gen = MetricDocGenerator(service_name="test")
        gen.register("m1", "counter", "A counter")
        text = gen.render_text()
        assert "m1" in text

    def test_render_json(self):
        gen = MetricDocGenerator()
        gen.register("m1", "gauge", "A gauge")
        docs = gen.render_json()
        assert docs[0]["type"] == "gauge"

    def test_render_markdown(self):
        gen = MetricDocGenerator()
        gen.register("m1", "histogram", "Latency", labels=["method"])
        md = gen.render_markdown()
        assert "| m1 |" in md

    def test_scan_collector(self):
        c = MetricsCollector(service_name="svc")
        c.counter("requests", "Total").inc(method="GET")
        c.gauge("connections", "Active").set(5)
        c.histogram("latency", "Response time").observe(0.1, method="GET")
        gen = MetricDocGenerator()
        found = gen.scan_collector(c)
        assert found == 3

    def test_empty_docs(self):
        gen = MetricDocGenerator()
        assert gen.render_text() == "(no metrics documented)"

    def test_metric_names_sorted(self):
        gen = MetricDocGenerator()
        gen.register("z_metric", "counter")
        gen.register("a_metric", "gauge")
        assert gen.metric_names == ["a_metric", "z_metric"]

    def test_doc_to_dict(self):
        d = MetricDoc(name="test", metric_type="gauge", labels=["env"], unit="ms")
        data = d.to_dict()
        assert data["unit"] == "ms"


class TestSelfHealingCollector:

    def test_register_and_run(self):
        h = SelfHealingCollector(base_backoff=0.001)
        h.register("cpu", lambda: {"usage": 0.75})
        result = h.run("cpu")
        assert result == {"usage": 0.75}

    def test_run_unknown(self):
        h = SelfHealingCollector()
        assert h.run("unknown") is None

    def test_auto_retry_on_failure(self):
        call_count = [0]
        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("transient")
            return "ok"
        h = SelfHealingCollector(max_retries=3, base_backoff=0.001)
        h.register("flaky", flaky)
        result = h.run("flaky")
        assert result == "ok"

    def test_exhaust_retries(self):
        h = SelfHealingCollector(max_retries=2, base_backoff=0.001)
        h.register("bad", lambda: 1/0)
        result = h.run("bad")
        assert result is None
        status = h.get_status("bad")
        assert status["status"] == "failed"

    def test_run_all(self):
        h = SelfHealingCollector(base_backoff=0.001)
        h.register("a", lambda: 1)
        h.register("b", lambda: 2)
        results = h.run_all()
        assert results == {"a": 1, "b": 2}

    def test_unregister(self):
        h = SelfHealingCollector()
        h.register("a", lambda: 1)
        assert h.unregister("a") is True
        assert h.unregister("a") is False

    def test_healthy_and_failed_count(self):
        h = SelfHealingCollector(max_retries=0, base_backoff=0.001)
        h.register("good", lambda: 1)
        h.register("bad", lambda: 1/0)
        h.run("good")
        h.run("bad")
        assert h.healthy_count == 1
        assert h.failed_count == 1

    def test_reset(self):
        h = SelfHealingCollector(max_retries=0, base_backoff=0.001)
        h.register("bad", lambda: 1/0)
        h.run("bad")
        assert h.reset("bad") is True
        assert h.get_status("bad")["status"] == "idle"

    def test_reset_all(self):
        h = SelfHealingCollector(max_retries=0, base_backoff=0.001)
        h.register("a", lambda: 1/0)
        h.register("b", lambda: 1/0)
        h.run_all()
        h.reset_all()
        assert h.failed_count == 0

    def test_get_all_status(self):
        h = SelfHealingCollector(base_backoff=0.001)
        h.register("a", lambda: 1)
        h.register("b", lambda: 2)
        statuses = h.get_all_status()
        assert "a" in statuses
        assert "b" in statuses


class TestMetricExporter:

    def _make_collector(self):
        c = MetricsCollector(service_name="test_svc")
        c.counter("requests", "Total").inc(method="GET")
        c.gauge("active", "Active conns").set(5)
        c.histogram("latency", "Response time").observe(0.1)
        return c

    def test_export_json(self):
        c = self._make_collector()
        exp = MetricExporter()
        result = exp.export_json(c)
        data = json.loads(result)
        assert "metrics" in data
        assert data["service"] == "test_svc"

    def test_export_csv(self):
        c = self._make_collector()
        exp = MetricExporter()
        result = exp.export_csv(c)
        assert "name,type,value,labels" in result

    def test_export_prometheus(self):
        c = self._make_collector()
        exp = MetricExporter()
        result = exp.export_prometheus(c)
        assert "# TYPE" in result

    def test_export_text(self):
        c = self._make_collector()
        exp = MetricExporter()
        result = exp.export_text(c)
        assert "test_svc" in result

    def test_export_dict(self):
        c = self._make_collector()
        exp = MetricExporter()
        result = exp.export_dict(c)
        assert "service" in result

    def test_export_format_dispatch(self):
        c = self._make_collector()
        exp = MetricExporter()
        for fmt in [ExportFormat.JSON, ExportFormat.CSV, ExportFormat.PROMETHEUS, ExportFormat.TEXT]:
            result = exp.export(c, fmt)
            assert len(result) > 0

    def test_export_without_timestamp(self):
        c = self._make_collector()
        exp = MetricExporter(include_timestamp=False)
        result = json.loads(exp.export_json(c))
        assert "exported_at" not in result
