"""UI-free analysis pipeline and detector registry."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from threading import Event
import statistics
import time
from typing import Callable

from .checks import Context, Detector
from .checks.content import ContentDetector
from .checks.structure import StructureDetector
from .loaders import Dataset
from .models import AnalysisOptions, AnalysisResult, ColumnProfile
from .semantics import numeric, date_value, missing_kind, leading_zero, DATE_SHAPE


def profile_column(name, dtype, values, options):
    kinds = [missing_kind(v, options.null_tokens) for v in values]
    valid = [v for v, kind in zip(values, kinds) if kind is None]
    strings = [str(v) for v in valid]
    distinct = len(set(strings))
    inferred = "string"
    numbers = [n for v in valid if (n := numeric(v)) is not None]
    dates = [d for v in valid if (d := date_value(v)) is not None]
    stats = {}
    if not valid:
        inferred = "empty"
    elif all(isinstance(v, bool) for v in valid):
        inferred = "boolean"
    elif len(dates) / len(valid) >= options.dominance or sum(bool(DATE_SHAPE.fullmatch(s.strip())) for s in strings) / len(valid) >= options.dominance:
        inferred = "date"
        if dates:
            stats = {"min": min(dates).isoformat(), "max": max(dates).isoformat(), "parsed": len(dates)}
    elif len(numbers) / len(valid) >= options.dominance and sum(leading_zero(v) for v in valid) / len(valid) < .5:
        inferred = "number"
        try:
            mean = statistics.mean(numbers)
        except OverflowError:
            mean = "вне диапазона float"
        stats = {"min": min(numbers), "max": max(numbers), "mean": mean, "median": statistics.median(numbers), "parsed": len(numbers)}
    elif strings:
        stats = {"min_length": min(map(len, strings)), "max_length": max(map(len, strings))}
    # Avoid suggesting a majority type from tiny ambiguous examples.
    if len(valid) < 5 and inferred in ("number", "date") and len(numbers if inferred == "number" else dates) != len(valid):
        inferred = "string"
        stats = {}
    return ColumnProfile(name, str(dtype), inferred, sum(k is not None for k in kinds),
        kinds.count("null"), kinds.count("blank"), kinds.count("marker"), distinct,
        distinct / len(valid) if valid else 0, stats, Counter(strings).most_common(5)), [k is not None for k in kinds]


class Analyzer:
    """Inject additional detectors without coupling Rule Miner/Table DNA to Qt."""
    def __init__(self, detectors: list[Detector] | None = None):
        self.detectors = detectors if detectors is not None else [StructureDetector(), ContentDetector()]

    def analyze(self, dataset: Dataset, options: AnalysisOptions | None = None,
                progress: Callable[[int, str], None] | None = None,
                cancel: Event | None = None) -> AnalysisResult:
        started = time.perf_counter()
        options = options or AnalysisOptions()
        if not .5 < options.dominance <= 1 or options.example_rows < 1 or options.iqr_multiplier <= 0:
            raise ValueError("Недопустимые параметры анализа.")
        progress = progress or (lambda *_: None)
        context = Context(dataset, options, [], {}, {}, cancel or Event())
        frame = dataset.frame
        for i, name in enumerate(frame.columns):
            context.checkpoint()
            values = frame[name].to_list()
            profile, missing = profile_column(name, frame[name].dtype, values, options)
            context.values[name] = values
            context.missing[name] = missing
            context.profiles.append(profile)
            progress(int(5 + 45 * (i + 1) / max(frame.width, 1)), f"Профиль: {name}")
        findings = []
        for i, detector in enumerate(self.detectors):
            context.checkpoint()
            progress(55 + int(35 * i / max(1, len(self.detectors))), "Поиск находок…")
            findings.extend(detector.run(context))
        weights = {"high": 80, "medium": 55, "low": 30, "info": 5}
        for f in findings:
            context.checkpoint()
            f.score = weights[f.severity] + min(15, round(15 * f.count / max(1, f.total))) + (5 if f.confidence == "fact" else 0)
            for index in f.row_indices[:options.example_rows]:
                # Values are kept intact here; renderers always escape and limit display size.
                f.examples.append({"row": index + 1, "values": {name: context.values[name][index] for name in f.columns}})
        findings.sort(key=lambda f: (-f.score, f.code, f.columns))
        notes = list(dataset.notes)
        notes.append("Номер записи — 1-based позиция после заголовка; для CSV это не номер физической строки, если есть переносы или пустые записи.")
        notes.append(f"Тип — эвристика с порогом {options.dominance:.0%}; ключи проверены на текущем фрагменте. Составные ключи: только пары первых {options.max_key_columns} подходящих полей.")
        if not frame.height:
            notes.append("Таблица содержит только заголовок. Находок нет, поскольку нет записей.")
        result = AnalysisResult(dataset.source, dataset.format, frame.height, frame.width, dataset.truncated,
                                context.profiles, findings, notes, options, elapsed_seconds=time.perf_counter() - started)
        progress(100, "Готово")
        return result
