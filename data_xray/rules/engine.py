"""Safe predicates and bounded evaluation. This module never imports Qt."""
from __future__ import annotations

import re
import math
import time
from datetime import datetime
import polars as pl
from threading import Event
from typing import Callable

from ..checks import AnalysisCancelled
from ..loaders import Dataset
from ..semantics import missing_kind, numeric, date_value, shape
from .models import EvaluationOptions, MiningResult, RuleEvaluation, RuleSpec, MAX_RULES, validate_rule, date_format_pattern
from .numbers import exact_numeric


class _Context:
    def __init__(self, dataset, options, cancel=None):
        self.dataset = dataset
        self.options = options
        self.cancel = cancel
        self.values = {}
        self.missing = {}
        self.parsed = {}
        self.matches = {}

    def checkpoint(self):
        if self.cancel is not None and self.cancel.is_set():
            raise AnalysisCancelled("Поиск/проверка правил отменены.")

    def column(self, name):
        self.checkpoint()
        if name not in self.values:
            values = self.dataset.frame[name].to_list()
            missing = []
            for i, value in enumerate(values):
                if i % 512 == 0:
                    self.checkpoint()
                missing.append(missing_kind(value, self.options.null_tokens) is not None)
            self.values[name] = values
            self.missing[name] = missing
        return self.values[name], self.missing[name]

    def parse(self, name, kind):
        key = (name, kind)
        if key not in self.parsed:
            values, missing = self.column(name)
            parser = {"number": numeric, "exact_number": exact_numeric}.get(kind, date_value)
            result = []
            for i, (value, absent) in enumerate(zip(values, missing)):
                if i % 512 == 0:
                    self.checkpoint()
                result.append(None if absent else parser(value))
            self.parsed[key] = result
        return self.parsed[key]

    def regex(self, name, pattern):
        key = (name, pattern)
        if key not in self.matches:
            values, _ = self.column(name)
            self.checkpoint()
            self.matches[key] = pl.Series([None if value is None else str(value) for value in values], dtype=pl.String).str.contains(pattern, strict=True).fill_null(False).to_list()
            self.checkpoint()
        return self.matches[key]

    def date_matches(self, name, fmt):
        key = (name, 'date_format', fmt)
        if key not in self.parsed:
            values, _ = self.column(name)
            matches = self.regex(name, date_format_pattern(fmt))
            result = []
            for i, (value, matched) in enumerate(zip(values, matches)):
                if i % 512 == 0:
                    self.checkpoint()
                try:
                    if matched:
                        datetime.strptime(str(value), fmt)
                    result.append(bool(matched))
                except ValueError:
                    result.append(False)
            self.parsed[key] = result
        return self.parsed[key]


def _format_matches(value, params):
    text = str(value)
    fmt = params["format"]
    if fmt == "shape":
        return shape(value) == params["shape"]
    if fmt == "digits":
        return bool(text) and text.isascii() and text.isdigit()
    if fmt == "email":
        # Deliberately simple syntax predicate, no claim of deliverability.
        return re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text) is not None
    if fmt == "uuid":
        return re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", text) is not None
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is not None and date_value(text) is not None


def _unique_value(value):
    """Hash loaded scalars and nested Polars values without string coercion."""
    if isinstance(value, float) and math.isnan(value):
        # Polars groups NaN values together; missing_kind treats numeric NaN as
        # filled (unlike the textual marker "nan").
        return (float, "nan")
    if isinstance(value, dict):
        return (dict, tuple((key, _unique_value(item)) for key, item in sorted(value.items())))
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_unique_value(item) for item in value))
    return (type(value), value)


def _unique_key(columns, row):
    return tuple(_unique_value(values[row]) for values, _ in columns)


def _unique_storage_dtype(dtype):
    """Keep temporal storage precision: Python datetime truncates nanoseconds."""
    if dtype.is_temporal():
        return pl.Int64
    if isinstance(dtype, pl.List):
        return pl.List(_unique_storage_dtype(dtype.inner))
    if isinstance(dtype, pl.Array):
        return pl.Array(_unique_storage_dtype(dtype.inner), dtype.size)
    if isinstance(dtype, pl.Struct):
        return pl.Struct([pl.Field(field.name, _unique_storage_dtype(field.dtype)) for field in dtype.fields])
    return dtype


def _evaluate(context: _Context, rule: RuleSpec) -> RuleEvaluation:
    context.checkpoint()
    validate_rule(rule)
    n = context.dataset.frame.height
    missing_columns = [name for name in rule.columns if name not in context.dataset.frame.columns]
    if missing_columns:
        return RuleEvaluation(rule, n, 0, 0, 0, n, None, "not_applicable",
                              evidence={"missing_columns": missing_columns},
                              message="Отсутствуют столбцы: " + ", ".join(missing_columns))
    columns = [context.column(name) for name in rule.columns]
    a, ma = columns[0]
    b, mb = columns[1] if len(columns) == 2 else (None, None)
    p = rule.params
    bounds = tuple(exact_numeric(p[key]) for key in ('lower', 'upper')) if rule.kind == 'numeric_constraint' else None
    parsed = None
    if rule.kind in ("robust_range", "numeric_constraint", "comparison"):
        parse_kind = p.get("type", "number")
        if parse_kind == "number" and rule.kind != "robust_range":
            parse_kind = "exact_number"
        parsed = [context.parse(name, parse_kind) for name in rule.columns]
    matches = context.regex(rule.columns[0], r"\A(?:" + p['pattern'] + r")\z") if rule.kind == 'regex' else context.date_matches(rule.columns[0], p['format']) if rule.kind == 'date_format' else None
    allowed = set(p["values"]) if rule.kind == "allowed_values" else None
    unique_counts = {}
    if rule.kind == "unique":
        key_columns = []
        for name, (values, missing) in zip(rule.columns, columns):
            context.checkpoint()
            series = context.dataset.frame[name]
            storage_dtype = _unique_storage_dtype(series.dtype)
            key_columns.append((series.cast(storage_dtype).to_list() if storage_dtype != series.dtype else values, missing))
        # Store counts, never a list of every row in each duplicate group.
        for i in range(n):
            if i % 512 == 0:
                context.checkpoint()
            if any(missing[i] for _, missing in columns):
                continue
            key = _unique_key(key_columns, i)
            unique_counts[key] = unique_counts.get(key, 0) + 1
    counts = {"missing_rows": 0, "invalid_rows": 0, "unseen_determinants": 0, "condition_not_met": 0}
    evaluated = passed = violations = 0
    indices = []
    examples = []
    for i in range(n):
        if i % 512 == 0:
            context.checkpoint()
        reason = "rule_violation"
        if rule.kind == "not_null":
            ok = not ma[i]
            if not ok:
                counts["missing_rows"] += 1
                reason = "missing"
        elif rule.kind == "conditional_required":
            if ma[i] or str(a[i]) != p["value"]:
                counts["condition_not_met"] += 1
                continue
            ok = not mb[i]
            if not ok:
                counts["missing_rows"] += 1
                reason = "missing_required"
        else:
            if any(missing[i] for _, missing in columns):
                counts["missing_rows"] += 1
                continue
            if rule.kind == "unique":
                ok = unique_counts[_unique_key(key_columns, i)] == 1
                reason = "duplicate_value" if len(columns) == 1 else "duplicate_combination"
            elif rule.kind == "allowed_values":
                ok = str(a[i]) in allowed
            elif rule.kind == "string_shape":
                ok = _format_matches(a[i], p)
            elif rule.kind in ("regex", "date_format"):
                ok = matches[i]
                if not ok and rule.kind == 'date_format':
                    counts['invalid_rows'] += 1
                    reason = 'invalid_date'
            elif rule.kind == "functional_dependency":
                key = str(a[i])
                if key not in p["mapping"]:
                    counts["unseen_determinants"] += 1
                    reason = "unseen_determinant"
                    ok = False
                else:
                    ok = str(b[i]) == p["mapping"][key]
            else:
                left = parsed[0][i]
                right = parsed[1][i] if rule.kind == "comparison" else None
                if left is None or rule.kind == "comparison" and right is None:
                    counts["invalid_rows"] += 1
                    reason = "invalid_number" if p.get("type", "number") == "number" else "invalid_date"
                    ok = False
                elif rule.kind == "robust_range":
                    ok = p["lower"] <= left <= p["upper"]
                elif rule.kind == "numeric_constraint":
                    lower, upper = bounds
                    ok = ((lower is None or (left >= lower if p['lower_inclusive'] else left > lower)) and
                          (upper is None or (left <= upper if p['upper_inclusive'] else left < upper)))
                elif p["operator"] == "<=":
                    ok = left <= right
                elif p["operator"] == ">=":
                    ok = left >= right
                else:
                    ok = left == right
        evaluated += 1
        if ok:
            passed += 1
        else:
            violations += 1
            if len(indices) < context.options.max_violation_rows:
                indices.append(i)
            if len(examples) < context.options.example_rows:
                examples.append({"row": i + 1, "values": {name: context.values[name][i] for name in rule.columns}, "reason": reason})
    status = "not_applicable" if evaluated == 0 else "fail" if violations else "pass"
    message = "Нет применимых записей; правило не подтверждено." if evaluated == 0 else ""
    return RuleEvaluation(rule, n, evaluated, passed, violations, n - evaluated,
                          passed / evaluated if evaluated else None, status, indices, examples, counts, message)


def evaluate_rules(dataset: Dataset, rules: list[RuleSpec], options: EvaluationOptions | None = None,
                   progress: Callable[[int, str], None] | None = None, cancel: Event | None = None) -> MiningResult:
    started = time.perf_counter()
    options = options or EvaluationOptions()
    if not isinstance(options, EvaluationOptions):
        raise ValueError("Ожидались EvaluationOptions.")
    if not isinstance(rules, (list, tuple)) or len(rules) > MAX_RULES:
        raise ValueError("За один запуск допускается до 500 правил.")
    if any(not isinstance(rule, RuleSpec) for rule in rules):
        raise ValueError("Ожидался список RuleSpec.")
    context = _Context(dataset, options, cancel)
    context.checkpoint()
    progress = progress or (lambda *_: None)
    result = []
    for i, rule in enumerate(rules):
        result.append(_evaluate(context, rule))
        progress(int(100 * (i + 1) / max(1, len(rules))), f"Проверка: {rule.description}")
    context.checkpoint()
    notes = list(dataset.notes) + ["Проверены явные сохраненные условия. Пропуски вне not_null/conditional_required пропускаются; неразбираемые заполненные числа и даты нарушают правило.",
        "row_indices — позиции с 0; examples.row — номера записей с 1 после заголовка, не физические строки CSV."]
    if dataset.truncated:
        notes.append("Проверен только загруженный фрагмент: результат не распространяется на непрочитанные строки.")
    progress(100, "Правила проверены")
    context.checkpoint()
    return MiningResult(dataset.source, dataset.frame.height, result, notes, options, time.perf_counter() - started)
