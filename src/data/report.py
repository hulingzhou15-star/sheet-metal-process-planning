"""Machine-readable diagnostics and an output writer restricted to derived data."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Any

from .schema import SourceLocation


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    entity_id: str | None = None
    source: SourceLocation | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()
    statistics: dict[str, Any] = field(default_factory=dict)
    checked_rules: tuple[str, ...] = ()
    source_path: str | None = None
    source_sha256: str | None = None
    distance_tolerance: float | None = None

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(issue.severity for issue in self.issues)
        return {severity.value: counts[severity] for severity in Severity}

    @property
    def passed(self) -> bool:
        return self.counts[Severity.ERROR.value] == 0

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "passed": self.passed, "counts": self.counts,
                "source_path": self.source_path, "source_sha256": self.source_sha256,
                "distance_tolerance": self.distance_tolerance,
                "statistics": self.statistics, "checked_rules": list(self.checked_rules),
                "issues": [asdict(issue) for issue in self.issues]}


def write_report(report: ValidationReport, path: str | Path, *, project_root: str | Path) -> Path:
    """Only write JSON below outputs/ or data/processed/, resolving symlinks first."""
    root = Path(project_root).resolve()
    target = Path(path).resolve()
    allowed = (root / "outputs", root / "data" / "processed")
    protected = tuple((root / p).resolve() for p in (
        "data/raw", "data/parts", "data/supervision", "baselines",
        "information_extract/sources", "information_extract/projects"))
    if any(target.is_relative_to(p) for p in protected) or not any(target.is_relative_to(p) for p in allowed):
        raise ValueError("Reports may only be written below outputs/ or data/processed/")
    if target.suffix.lower() != ".json":
        raise ValueError("The report destination must be a .json file")
    # Refuse existing hard links to protected input files as well as path aliases.
    if target.exists():
        if any(target.samefile(p) for folder in protected for p in folder.rglob("*") if p.is_file()):
            raise ValueError("Report destination aliases a protected raw input file")
    text = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    return target
