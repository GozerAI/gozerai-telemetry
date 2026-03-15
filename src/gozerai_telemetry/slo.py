"""SLO/SLI tracking utilities.

Service Level Indicators (SLIs) track good/total event ratios.
Service Level Objectives (SLOs) pair an SLI with a target.
Zero dependencies. Thread-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Optional


@dataclass
class SLI:
    """Service Level Indicator — tracks good/total events."""

    name: str
    _good: int = field(default=0, repr=False)
    _total: int = field(default=0, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_good(self) -> None:
        with self._lock:
            self._good += 1
            self._total += 1

    def record_bad(self) -> None:
        with self._lock:
            self._total += 1

    @property
    def ratio(self) -> float:
        """Good/total ratio. Returns 0.0 if no events recorded."""
        with self._lock:
            if self._total == 0:
                return 0.0
            return self._good / self._total

    def reset(self) -> None:
        with self._lock:
            self._good = 0
            self._total = 0


@dataclass
class SLO:
    """Service Level Objective — an SLI paired with a target."""

    name: str
    sli: SLI
    target: float  # e.g. 0.999

    @property
    def met(self) -> bool:
        """True if the SLI ratio meets or exceeds the target."""
        return self.sli.ratio >= self.target

    @property
    def error_budget_remaining(self) -> float:
        """Remaining error budget. Can be negative if over-budget.

        Formula: ratio - target when reframed as budget.
        If target=0.999 and ratio=1.0, budget = 1.0 - 0.999 - (1.0 - 1.0) = 0.001
        More precisely: allowed_errors = 1 - target, actual_errors = 1 - ratio
        remaining = allowed_errors - actual_errors = ratio - target
        """
        return self.sli.ratio - self.target

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "current_ratio": self.sli.ratio,
            "met": self.met,
            "error_budget_remaining": self.error_budget_remaining,
        }


class SLOTracker:
    """Manages multiple SLOs for a service."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self._slos: Dict[str, SLO] = {}

    def register(self, name: str, target: float) -> SLO:
        """Register a new SLO with its own SLI."""
        sli = SLI(name=name)
        slo = SLO(name=name, sli=sli, target=target)
        self._slos[name] = slo
        return slo

    def get(self, name: str) -> Optional[SLO]:
        return self._slos.get(name)

    def report(self) -> dict:
        """Report on all tracked SLOs."""
        return {
            "service": self.service_name,
            "slos": {name: slo.to_dict() for name, slo in self._slos.items()},
            "all_met": all(slo.met for slo in self._slos.values()) if self._slos else True,
        }
