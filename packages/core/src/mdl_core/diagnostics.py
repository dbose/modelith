"""Diagnostics: MDL error codes and severity (spec §2.4, §10 exit codes).

Every check emits Diagnostic objects. Severity drives exit codes so CI can gate:
  0 ok, 1 validation error (spec §10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "info": 1}[self.value]


@dataclass(frozen=True)
class Diagnostic:
    code: str  # e.g. MDL-E101
    severity: Severity
    message: str
    path: str | None = None  # file path or ULID
    fix_available: bool = False


@dataclass
class DiagnosticSet:
    items: list[Diagnostic] = field(default_factory=list)

    def add(self, d: Diagnostic) -> None:
        self.items.append(d)

    def extend(self, ds: DiagnosticSet) -> None:
        self.items.extend(ds.items)

    def by_min_severity(self, minimum: Severity) -> list[Diagnostic]:
        return [d for d in self.items if d.severity.rank >= minimum.rank]

    def has(self, minimum: Severity) -> bool:
        return any(d.severity.rank >= minimum.rank for d in self.items)

    @property
    def max_severity(self) -> Severity | None:
        if not self.items:
            return None
        return max((d.severity for d in self.items), key=lambda s: s.rank)
