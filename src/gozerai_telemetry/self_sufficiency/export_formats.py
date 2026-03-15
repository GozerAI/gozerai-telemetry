"""Export metrics in JSON, CSV, and Prometheus text format.

Provides a unified MetricExporter for multiple output formats.
Zero external dependencies.
"""

from __future__ import annotations

import csv
import io
import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ExportFormat(Enum):
    JSON = "json"
    CSV = "csv"
    PROMETHEUS = "prometheus"
    TEXT = "text"


class MetricExporter:

    def __init__(self, service_name="", include_timestamp=True):
        self.service_name = service_name
        self.include_timestamp = include_timestamp

    def export(self, collector, fmt=ExportFormat.JSON):
        dispatch = {
            ExportFormat.JSON: self.export_json,
            ExportFormat.CSV: self.export_csv,
            ExportFormat.PROMETHEUS: self.export_prometheus,
            ExportFormat.TEXT: self.export_text,
        }
        fn = dispatch.get(fmt)
        if fn is None:
            raise ValueError(f"Unsupported format: {fmt}")
        return fn(collector)

    def export_json(self, collector):
        data = self._collect_metrics(collector)
        output = {
            "metrics": data,
            "service": self.service_name or getattr(collector, "service_name", "unknown"),
        }
        if self.include_timestamp:
            output["exported_at"] = time.time()
        return json.dumps(output, indent=2, default=str)

    def export_csv(self, collector):
        rows = self._collect_flat_rows(collector)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "type", "value", "labels"])
        for row in rows:
            writer.writerow(row)
        return buf.getvalue()

    def export_prometheus(self, collector):
        if hasattr(collector, "to_prometheus"):
            return collector.to_prometheus()
        return self._build_prometheus(collector)

    def export_text(self, collector):
        data = self._collect_metrics(collector)
        svc = self.service_name or getattr(collector, "service_name", "unknown")
        lines = [f"Metrics for {svc}", "=" * 40]
        for item in data:
            name = item.get("name", "unknown")
            mtype = item.get("type", "unknown")
            lines.append("")
            lines.append(f"{name} ({mtype})")
            for k, v in item.items():
                if k not in ("name", "type"):
                    lines.append(f"  {k}: {v}")
        return chr(10).join(lines)

    def export_dict(self, collector):
        if hasattr(collector, "to_dict"):
            return collector.to_dict()
        return {"metrics": self._collect_metrics(collector)}

    def _collect_metrics(self, collector):
        metrics = []
        for attr, mtype in [("_counters", "counter"), ("_gauges", "gauge"), ("_histograms", "histogram")]:
            items = getattr(collector, attr, {})
            for name, m in items.items():
                entry = {"name": name, "type": mtype, "description": getattr(m, "description", "")}
                if mtype in ("counter", "gauge"):
                    values = getattr(m, "_values", {})
                    if values:
                        entry["series"] = [{"labels": dict(k), "value": v} for k, v in values.items()]
                elif mtype == "histogram":
                    totals = getattr(m, "_totals", {})
                    sums = getattr(m, "_sums", {})
                    if totals:
                        entry["series"] = [{"labels": dict(k), "count": totals[k], "sum": sums.get(k, 0.0)} for k in totals]
                metrics.append(entry)
        return metrics

    def _collect_flat_rows(self, collector):
        rows = []
        for attr, mtype in [("_counters", "counter"), ("_gauges", "gauge")]:
            items = getattr(collector, attr, {})
            for name, m in items.items():
                values = getattr(m, "_values", {})
                if values:
                    for k, v in values.items():
                        ls = ";".join(f"{lk}={lv}" for lk, lv in k) if k else ""
                        rows.append([name, mtype, str(v), ls])
                else:
                    rows.append([name, mtype, "0", ""])
        histograms = getattr(collector, "_histograms", {})
        for name, h in histograms.items():
            totals = getattr(h, "_totals", {})
            sums = getattr(h, "_sums", {})
            for k in totals:
                ls = ";".join(f"{lk}={lv}" for lk, lv in k) if k else ""
                rows.append([name, "histogram", f"count={totals[k]},sum={sums.get(k,0)}", ls])
        return rows

    def _build_prometheus(self, collector):
        lines = []
        for m in self._collect_metrics(collector):
            name, mtype = m["name"], m["type"]
            desc = m.get("description", "")
            if desc:
                lines.append(f"# HELP {name} {desc}")
            lines.append(f"# TYPE {name} {mtype}")
            for series in m.get("series", []):
                labels = series.get("labels", {})
                if labels:
                    lp = "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
                else:
                    lp = ""
                val = series.get("value", series.get("count", 0))
                lines.append(f"{name}{lp} {val}")
        return chr(10).join(lines)
