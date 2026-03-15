"""Self-contained text-based metric dashboard.

Renders metrics as ASCII tables and simple bar charts.
Zero external dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class DashboardPanel:
    title: str
    metric_fn: Callable[[], Dict[str, Any]]
    display_type: str = "table"
    width: int = 40

    def render(self):
        data = self.metric_fn()
        if self.display_type == "bar":
            return self._render_bar(data)
        if self.display_type == "sparkline":
            return self._render_sparkline(data)
        return self._render_table(data)

    def _render_table(self, data):
        lines = [f"  {self.title}  ".center(self.width, "=")]
        if not data:
            lines.append("  (no data)")
            return chr(10).join(lines)
        max_key = max(len(str(k)) for k in data)
        for k, v in data.items():
            lines.append(f"  {str(k).ljust(max_key)}  {v}")
        return chr(10).join(lines)

    def _render_bar(self, data):
        lines = [f"  {self.title}  ".center(self.width, "=")]
        if not data:
            lines.append("  (no data)")
            return chr(10).join(lines)
        numeric = {k: v for k, v in data.items() if isinstance(v, (int, float))}
        if not numeric:
            return self._render_table(data)
        max_val = max(numeric.values()) or 1
        bar_width = self.width - 20
        max_key = max(len(str(k)) for k in numeric)
        for k, v in numeric.items():
            bar_len = int((v / max_val) * bar_width)
            lines.append(f"  {str(k).ljust(max_key)} |{'#' * bar_len} {v}")
        return chr(10).join(lines)

    def _render_sparkline(self, data):
        lines = [f"  {self.title}  ".center(self.width, "=")]
        spark_chars = " _.-~*"
        values = [float(v) for v in data.values() if isinstance(v, (int, float))]
        if not values:
            lines.append("  (no data)")
            return chr(10).join(lines)
        min_v, max_v = min(values), max(values)
        spread = max_v - min_v if max_v > min_v else 1
        spark = ""
        for v in values:
            idx = int((v - min_v) / spread * (len(spark_chars) - 1))
            spark += spark_chars[idx]
        lines.append(f"  {spark}")
        lines.append(f"  min={min_v:.2f}  max={max_v:.2f}")
        return chr(10).join(lines)


class TextDashboard:

    def __init__(self, title="Telemetry Dashboard", width=60):
        self.title = title
        self.width = width
        self._panels: List[DashboardPanel] = []

    def add_panel(self, title, metric_fn, display_type="table"):
        panel = DashboardPanel(title=title, metric_fn=metric_fn,
                               display_type=display_type, width=self.width)
        self._panels.append(panel)
        return panel

    def remove_panel(self, title):
        for i, p in enumerate(self._panels):
            if p.title == title:
                self._panels.pop(i)
                return True
        return False

    def render(self):
        fmt = chr(37) + "Y-" + chr(37) + "m-" + chr(37) + "d " + chr(37) + "H:" + chr(37) + "M:" + chr(37) + "S"
        header = f"  {self.title}  ".center(self.width, "#")
        timestamp = f"  {time.strftime(fmt)}  ".center(self.width)
        sections = [header, timestamp, ""]
        for panel in self._panels:
            try:
                sections.append(panel.render())
                sections.append("")
            except Exception as exc:
                sections.append(f"  [{panel.title}: error: {exc}]")
                sections.append("")
        sections.append("#" * self.width)
        return chr(10).join(sections)

    @property
    def panel_count(self):
        return len(self._panels)

    @property
    def panel_titles(self):
        return [p.title for p in self._panels]
