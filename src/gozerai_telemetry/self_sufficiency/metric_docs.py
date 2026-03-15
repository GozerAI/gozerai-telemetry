"""Auto-generate documentation for registered metrics.

Inspects a MetricsCollector and produces structured documentation.
Zero external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MetricDoc:
    name: str
    metric_type: str
    description: str = ""
    labels: List[str] = field(default_factory=list)
    unit: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        result = {"name": self.name, "type": self.metric_type, "description": self.description}
        if self.labels:
            result["labels"] = self.labels
        if self.unit:
            result["unit"] = self.unit
        if self.extra:
            result["extra"] = self.extra
        return result

    def to_text(self):
        lines = [f"Metric: {self.name}", f"  Type: {self.metric_type}"]
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.labels:
            lines.append(f"  Labels: {', '.join(self.labels)}")
        if self.unit:
            lines.append(f"  Unit: {self.unit}")
        return chr(10).join(lines)


class MetricDocGenerator:

    def __init__(self, service_name=""):
        self.service_name = service_name
        self._docs: Dict[str, MetricDoc] = {}

    def register(self, name, metric_type, description="", labels=None, unit="", **extra):
        doc = MetricDoc(name=name, metric_type=metric_type, description=description,
                        labels=labels or [], unit=unit, extra=extra)
        self._docs[name] = doc
        return doc

    def scan_collector(self, collector):
        discovered = 0
        for attr, mtype in [("_counters", "counter"), ("_gauges", "gauge"), ("_histograms", "histogram")]:
            items = getattr(collector, attr, {})
            for name, m in items.items():
                labels = self._extract_labels(m)
                extra = {}
                if mtype == "histogram":
                    buckets = getattr(m, "_buckets", None)
                    if buckets:
                        extra["buckets"] = list(buckets)
                self.register(name, mtype, getattr(m, "description", ""), labels=labels, **extra)
                discovered += 1
        return discovered

    def _extract_labels(self, metric):
        labels_set = set()
        for attr in ("_values", "_counts", "_sums", "_totals"):
            d = getattr(metric, attr, {})
            for key in d:
                if isinstance(key, tuple):
                    for label_name, _ in key:
                        labels_set.add(label_name)
        return sorted(labels_set)

    def get_doc(self, name):
        return self._docs.get(name)

    @property
    def metric_count(self):
        return len(self._docs)

    @property
    def metric_names(self):
        return sorted(self._docs.keys())

    def render_text(self):
        if not self._docs:
            return "(no metrics documented)"
        header = "Metric Documentation"
        if self.service_name:
            header += f" - {self.service_name}"
        lines = [header, "=" * len(header), ""]
        for name in sorted(self._docs):
            lines.append(self._docs[name].to_text())
            lines.append("")
        return chr(10).join(lines)

    def render_json(self):
        return [self._docs[name].to_dict() for name in sorted(self._docs)]

    def render_markdown(self):
        if not self._docs:
            return "No metrics documented."
        lines = ["# Metric Documentation"]
        if self.service_name:
            lines[0] += f" - {self.service_name}"
        lines.append("")
        lines.append("| Name | Type | Description | Labels |")
        lines.append("|------|------|-------------|--------|")
        for name in sorted(self._docs):
            d = self._docs[name]
            labels = ", ".join(d.labels) if d.labels else "-"
            lines.append(f"| {d.name} | {d.metric_type} | {d.description} | {labels} |")
        return chr(10).join(lines)
