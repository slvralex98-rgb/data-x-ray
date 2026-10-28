from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AnalysisOptions:
    max_rows: int = 200_000
    example_rows: int = 8
    dominance: float = 0.9
    iqr_multiplier: float = 1.5
    max_key_columns: int = 12
    null_tokens: tuple[str, ...] = ("null", "none", "n/a", "na", "nan")


@dataclass
class ColumnProfile:
    name: str
    physical_type: str
    inferred_type: str
    missing: int
    nulls: int
    blanks: int
    markers: int
    distinct: int
    unique_ratio: float
    statistics: dict[str, Any] = field(default_factory=dict)
    top_values: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Finding:
    code: str
    title: str
    severity: str
    confidence: str
    columns: list[str]
    count: int
    total: int
    description: str
    recommendation: str
    row_indices: list[int] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    score: int = 0


@dataclass
class AnalysisResult:
    source: str
    source_format: str
    rows: int
    columns: int
    truncated: bool
    profiles: list[ColumnProfile]
    findings: list[Finding]
    notes: list[str]
    options: AnalysisOptions
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
