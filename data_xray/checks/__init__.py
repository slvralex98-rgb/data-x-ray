"""Detector plug-in contract. Future Rule Miner and Table DNA can reuse Context."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Protocol

from ..loaders import Dataset
from ..models import AnalysisOptions, ColumnProfile, Finding


class AnalysisCancelled(Exception):
    pass


@dataclass
class Context:
    dataset: Dataset
    options: AnalysisOptions
    profiles: list[ColumnProfile]
    values: dict[str, list]
    missing: dict[str, list[bool]]
    cancel: Event

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise AnalysisCancelled("Анализ отменен.")


class Detector(Protocol):
    def run(self, context: Context) -> list[Finding]: ...


def finding(code: str, title: str, severity: str, confidence: str,
            columns: list[str], indices: list[int], total: int,
            description: str, recommendation: str) -> Finding:
    return Finding(code, title, severity, confidence, columns, len(indices), total,
                   description, recommendation, indices[:1000])
