"""Versioned, bounded, strict JSON. Fingerprints summarize metadata, never whole data."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from ..loaders import Dataset
from ..models import AnalysisResult
from ..semantics import shape, date_value

FORMAT = "data-xray.table-passport"
VERSION = 1
MAX_BYTES = 16 * 1024 * 1024
MAX_COLUMNS = 500
MAX_TEXT = 4000
MAX_VALUE = 600
MAX_EVIDENCE = 2000
METHOD = (
    "Паспорт описывает только проанализированный фрагмент. Уникальность — доля различных "
    "среди заполненных значений; пропуски — доля от всех строк. Частые значения: до пяти "
    "текстовых представлений из готового анализа; шаблоны рассчитаны только по этим примерам. "
    "Числовые и календарные границы относятся к успешно разобранным значениям. "
    "Отпечаток схемы учитывает порядок, имена, физические и предполагаемые типы столбцов. "
    "Отпечаток профиля учитывает схему, число строк, усечение, параметры, сводные метрики "
    "и структурные находки; он исключает путь и время. Это не хэш всех данных. "
    "Равные отпечатки не доказывают равенство таблиц; порядок строк и связи значений между "
    "столбцами не проверяются. При равной частоте состав top-5 может зависеть от порядка строк."
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _fingerprints(p):
    schema = [{key: c[key] for key in ("name", "physical_type", "inferred_type")} for c in p["columns"]]
    profile = {key: p[key] for key in ("rows", "truncated", "options", "columns", "evidence")}
    return {"algorithm": "sha256-canonical-json-v1", "schema": _hash(schema), "profile": _hash(profile)}


def _clip(value, limit=MAX_TEXT):
    return str(value)[:limit]


def build_passport(dataset: Dataset, result: AnalysisResult) -> dict[str, Any]:
    """O(columns + bounded top values + findings); never reads dataset cells."""
    schema = dataset.frame.schema
    if (dataset.source != result.source or dataset.frame.height != result.rows or
            dataset.frame.width != result.columns or dataset.format != result.source_format or
            dataset.truncated != result.truncated or
            dataset.frame.columns != [p.name for p in result.profiles] or
            any(str(schema[p.name]) != p.physical_type for p in result.profiles)):
        raise ValueError("Результат анализа не соответствует текущему источнику.")
    columns = []
    for p in result.profiles:
        top = [{"value": _clip(v, MAX_VALUE), "value_hash": sha256(str(v).encode("utf-8")).hexdigest(),
                "truncated": len(str(v)) > MAX_VALUE, "count": count} for v, count in p.top_values[:5]]
        top.sort(key=lambda item: (-item["count"], item["value_hash"]))
        patterns = {}
        for item in top:
            # Never imply a clipped example gives a complete original pattern.
            if not item["truncated"]:
                pattern = shape(item["value"])[:MAX_VALUE]
                patterns[pattern] = patterns.get(pattern, 0) + item["count"]
        stats = {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                 for k, v in p.statistics.items()}
        columns.append({"name": p.name, "physical_type": p.physical_type, "inferred_type": p.inferred_type,
            "missing": p.missing, "nulls": p.nulls, "blanks": p.blanks, "markers": p.markers,
            "missing_ratio": p.missing / result.rows if result.rows else 0.0,
            "distinct": p.distinct, "unique_ratio": p.unique_ratio,
            "statistics": stats, "top_values": top,
            "sample_patterns": [{"pattern": k, "count": v} for k, v in sorted(patterns.items())]})
    evidence = [{"code": f.code, "columns": list(f.columns), "count": f.count,
        "total": f.total, "confidence": f.confidence, "description": _clip(f.description)}
        for f in result.findings if f.code in ("key", "composite_key", "duplicates")]
    evidence.sort(key=lambda f: (f["code"], f["columns"]))
    passport = {"format": FORMAT, "version": VERSION, "source": _clip(result.source),
        "source_format": _clip(result.source_format, 100), "created_at": result.created_at,
        "rows": result.rows, "truncated": result.truncated, "options": asdict(result.options),
        "columns": columns, "evidence": evidence, "notes": [_clip(n) for n in result.notes[:128]]}
    passport["options"]["null_tokens"] = list(passport["options"]["null_tokens"])
    passport["fingerprints"] = _fingerprints(passport)
    validate_passport(passport)
    return passport


def _require(ok, message="Некорректный паспорт таблицы."):
    if not ok:
        raise ValueError(message)


def _keys(obj, keys):
    _require(type(obj) is dict and set(obj) == set(keys.split()))


def _text(value, limit=MAX_TEXT):
    _require(type(value) is str and len(value) <= limit and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _integer(value, maximum=10**12):
    _require(type(value) is int and 0 <= value <= maximum)


def _number(value, lower=None, upper=None):
    _require(type(value) in (int, float) and math.isfinite(value))
    _require(lower is None or value >= lower)
    _require(upper is None or value <= upper)


def _bounded_tree(value, depth=0):
    _require(depth <= 12, "Слишком глубокая структура JSON паспорта.")
    if isinstance(value, dict):
        _require(len(value) <= MAX_EVIDENCE)
        for k, v in value.items():
            _text(k)
            _bounded_tree(v, depth + 1)
    elif isinstance(value, list):
        _require(len(value) <= MAX_EVIDENCE)
        for v in value:
            _bounded_tree(v, depth + 1)
    elif type(value) is str:
        _text(value)
    elif type(value) in (int, float):
        _number(value)
    else:
        _require(value is None or type(value) is bool)


def validate_passport(p):
    """Reject unsupported versions, unknown keys, forged fingerprints and invalid counts."""
    _bounded_tree(p)
    _keys(p, "format version source source_format created_at rows truncated options columns evidence notes fingerprints")
    _require(p["format"] == FORMAT and type(p["version"]) is int and p["version"] == VERSION,
             "Неподдерживаемый формат или версия паспорта.")
    for key in ("source", "created_at"):
        _text(p[key])
    _text(p["source_format"], 100)
    _integer(p["rows"])
    _require(type(p["truncated"]) is bool)
    _keys(p["options"], "max_rows example_rows dominance iqr_multiplier max_key_columns null_tokens")
    options = p["options"]
    for key in ("max_rows", "example_rows", "max_key_columns"):
        _integer(options[key])
    _require(options["max_rows"] > 0 and options["example_rows"] > 0)
    _number(options["dominance"], .5, 1)
    _require(options["dominance"] > .5)
    _number(options["iqr_multiplier"], 0)
    _require(options["iqr_multiplier"] > 0)
    _require(type(options["null_tokens"]) is list and len(options["null_tokens"]) <= 128)
    for token in options["null_tokens"]:
        _text(token, MAX_VALUE)
    _require(type(p["columns"]) is list and 1 <= len(p["columns"]) <= MAX_COLUMNS)
    names = set()
    for c in p["columns"]:
        _keys(c, "name physical_type inferred_type missing nulls blanks markers missing_ratio distinct unique_ratio statistics top_values sample_patterns")
        _text(c["name"])
        _require(bool(c["name"].strip()) and c["name"] not in names)
        names.add(c["name"])
        _text(c["physical_type"], 200)
        _require(c["inferred_type"] in ("number", "date", "string", "boolean", "empty"))
        for key in ("missing", "nulls", "blanks", "markers", "distinct"):
            _integer(c[key], p["rows"])
        _require(c["missing"] == c["nulls"] + c["blanks"] + c["markers"])
        valid = p["rows"] - c["missing"]
        _require(c["distinct"] <= valid)
        for key, expected in (("missing_ratio", c["missing"] / p["rows"] if p["rows"] else 0),
                              ("unique_ratio", c["distinct"] / valid if valid else 0)):
            _number(c[key], 0, 1)
            _require(math.isclose(c[key], expected, abs_tol=1e-12))
        stats = c["statistics"]
        _require(type(stats) is dict and set(stats) <= {"min", "max", "mean", "median", "parsed", "min_length", "max_length"})
        for key, value in stats.items():
            if key in ("parsed", "min_length", "max_length"):
                _integer(value)
                if key == "parsed":
                    _require(value <= valid)
            elif value is not None:
                if type(value) is str:
                    _text(value, MAX_VALUE)
                else:
                    _number(value)
        if c['inferred_type'] == 'date':
            _require(set(stats) <= {'min', 'max', 'parsed'})
            for key in ('min', 'max'):
                if stats.get(key) is not None:
                    _require(type(stats[key]) is str and date_value(stats[key]) is not None)
        elif c['inferred_type'] == 'number':
            _require(set(stats) <= {'min', 'max', 'mean', 'median', 'parsed'})
            for key in ('min', 'max', 'mean', 'median'):
                if stats.get(key) is not None:
                    _require(type(stats[key]) in (int, float) or
                             key == 'mean' and stats[key] == 'вне диапазона float')
        else:
            _require(set(stats) <= {'min_length', 'max_length'})
        for lower, upper in (('min', 'max'), ('min_length', 'max_length')):
            if stats.get(lower) is not None and stats.get(upper) is not None:
                lo, hi = stats[lower], stats[upper]
                if c['inferred_type'] == 'date':
                    lo, hi = date_value(lo), date_value(hi)
                _require(lo <= hi)
        _require(type(c["top_values"]) is list and len(c["top_values"]) <= 5)
        hashes = set()
        for item in c["top_values"]:
            _keys(item, "value value_hash truncated count")
            _text(item["value"], MAX_VALUE)
            _require(type(item["truncated"]) is bool)
            _digest(item["value_hash"])
            _require(item["value_hash"] not in hashes)
            hashes.add(item["value_hash"])
            if not item["truncated"]:
                _require(item["value_hash"] == sha256(item["value"].encode("utf-8")).hexdigest())
            _integer(item["count"], valid)
            _require(item["count"] > 0)
        _require(sum(v["count"] for v in c["top_values"]) <= valid)
        _require(len(c["top_values"]) == min(5, c["distinct"]))
        _require(type(c["sample_patterns"]) is list and len(c["sample_patterns"]) <= 5)
        for item in c["sample_patterns"]:
            _keys(item, "pattern count")
            _text(item["pattern"], MAX_VALUE)
            _integer(item["count"], valid)
        _require(sum(v["count"] for v in c["sample_patterns"]) <= sum(v['count'] for v in c['top_values']))
    _require(type(p["evidence"]) is list and len(p["evidence"]) <= MAX_EVIDENCE)
    for item in p["evidence"]:
        _keys(item, "code columns count total confidence description")
        _require(item["code"] in ("key", "composite_key", "duplicates"))
        _require(type(item["columns"]) is list and 1 <= len(item["columns"]) <= MAX_COLUMNS)
        _require(all(type(n) is str and n in names for n in item["columns"]))
        _require(len(set(item["columns"])) == len(item["columns"]))
        if item['code'] == 'key':
            _require(len(item['columns']) == 1)
        if item['code'] == 'composite_key':
            _require(len(item['columns']) == 2)
        _integer(item["count"], p["rows"])
        _require(type(item["total"]) is int and item["total"] == p["rows"])
        _require(item["confidence"] in ("fact", "heuristic"))
        _text(item["description"])
    _require(type(p["notes"]) is list and len(p["notes"]) <= 128)
    for note in p["notes"]:
        _text(note)
    _keys(p["fingerprints"], "algorithm schema profile")
    _require(p["fingerprints"] == _fingerprints(p), "Отпечатки паспорта не соответствуют его содержимому.")
    _require(len(_canonical(p).encode("utf-8")) <= MAX_BYTES, "Паспорт превышает лимит 16 МиБ.")
    return p


def _digest(value):
    _require(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value))


def save_passport(passport, path):
    validate_passport(passport)
    path = Path(path)
    encoded = json.dumps(passport, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    _require(len(encoded) <= MAX_BYTES, "Паспорт превышает лимит 16 МиБ.")
    from ..atomic import atomic_write_bytes
    atomic_write_bytes(path, encoded)
    return path


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        _require(key not in obj, "Повторяющийся ключ JSON.")
        obj[key] = value
    return obj


def _nonfinite(value):
    raise ValueError("JSON паспорта содержит нечисловое значение NaN/Infinity.")


def load_passport(path):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, "Паспорт превышает лимит 16 МиБ.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
        return validate_passport(value)
    except (UnicodeError, RecursionError, OverflowError, TypeError, KeyError) as exc:
        raise ValueError("Некорректный JSON паспорта.") from exc


def _delta(before, after):
    return {"baseline": before, "current": after, "delta": after - before}


def compare_passports(current, baseline):
    """Directional comparison: all deltas are current minus saved baseline."""
    validate_passport(current)
    validate_passport(baseline)
    old = {c["name"]: c for c in baseline["columns"]}
    new = {c["name"]: c for c in current["columns"]}
    changed = []
    for name in sorted(old.keys() & new.keys()):
        a, b = old[name], new[name]
        metrics = {}
        for key in ("physical_type", "inferred_type"):
            if a[key] != b[key]:
                metrics[key] = {"baseline": a[key], "current": b[key]}
        for key in ("missing", "nulls", "blanks", "markers", "missing_ratio", "distinct", "unique_ratio"):
            if a[key] != b[key]:
                metrics[key] = _delta(a[key], b[key])
        for key in sorted(a["statistics"].keys() | b["statistics"].keys()):
            av, bv = a["statistics"].get(key), b["statistics"].get(key)
            if av != bv:
                item = {"baseline": av, "current": bv}
                if type(av) in (int, float) and type(bv) in (int, float) and a["inferred_type"] == b["inferred_type"]:
                    difference = bv - av
                    if math.isfinite(difference):
                        item["delta"] = difference
                if key in ('min', 'max') and a['inferred_type'] == b['inferred_type'] == 'date' and av is not None and bv is not None:
                    item['delta_seconds'] = (date_value(bv) - date_value(av)).total_seconds()
                metrics["statistics." + key] = item
        distribution = _distribution(a, b, baseline["rows"], current["rows"])
        if distribution["changed"]:
            metrics["top_values"] = distribution
        if a["sample_patterns"] != b["sample_patterns"]:
            metrics["sample_patterns"] = {"baseline": a["sample_patterns"], "current": b["sample_patterns"]}
        if metrics:
            changed.append({"column": name, "changes": metrics})
    warnings = ["Дельты = текущий паспорт − эталон. Переименование показано как удаление и добавление.",
        "Top-5 не является полным распределением. Отсутствие значения в top-5 не означает нулевую частоту. "
        "Доли рассчитаны среди заполненных строк; сравниваются текстовые представления. "
        "Совпадение сводных метрик и отпечатков не доказывает равенство данных."]
    if current["truncated"] or baseline["truncated"]:
        warnings.append("Хотя бы один источник усечен: выводы относятся только к загруженным фрагментам.")
    if current["options"] != baseline["options"]:
        warnings.append("Параметры анализа различаются: типы, пропуски и другие метрики могут быть несопоставимы.")
    if current["source_format"] != baseline["source_format"]:
        warnings.append("Форматы источников различаются; физические типы зависят от способа загрузки.")
    def keys(p):
        return sorted([f["columns"] for f in p["evidence"] if f["code"] in ("key", "composite_key")])
    return {"baseline_source": baseline["source"], "current_source": current["source"],
        "rows": _delta(baseline["rows"], current["rows"]),
        "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
        "changed": changed, "column_order_changed": [n for n in old if n in new] != [n for n in new if n in old],
        "same_schema_fingerprint": current["fingerprints"]["schema"] == baseline["fingerprints"]["schema"],
        "same_profile_fingerprint": current["fingerprints"]["profile"] == baseline["fingerprints"]["profile"],
        "keys": {"baseline": keys(baseline), "current": keys(current)},
        "duplicates": {"baseline": [f for f in baseline["evidence"] if f["code"] == "duplicates"],
                       "current": [f for f in current["evidence"] if f["code"] == "duplicates"]},
        "warnings": warnings}


def _distribution(a, b, old_rows, new_rows):
    old = {v["value_hash"]: v for v in a["top_values"]}
    new = {v["value_hash"]: v for v in b["top_values"]}
    old_n, new_n = old_rows - a["missing"], new_rows - b["missing"]
    values = []
    for key in sorted(old.keys() | new.keys()):
        av, bv = old.get(key), new.get(key)
        before = av["count"] / old_n if av and old_n else None
        after = bv["count"] / new_n if bv and new_n else None
        values.append({"value": (bv or av)["value"], "value_hash": key,
            "baseline_share": before, "current_share": after,
            "delta": after - before if before is not None and after is not None else None})
    return {"changed": a["top_values"] != b["top_values"] or old_n != new_n,
        "values": values,
        "baseline_covered_share": sum(v["count"] for v in old.values()) / old_n if old_n else 0,
        "current_covered_share": sum(v["count"] for v in new.values()) / new_n if new_n else 0,
        "known_shared_mass": sum(min(old[k]["count"] / old_n, new[k]["count"] / new_n)
                                 for k in old.keys() & new.keys()) if old_n and new_n else 0}
