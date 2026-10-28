"""Versioned data-only JSON. Rust regex is allowed; code and SQL never are."""
from __future__ import annotations

import json
from pathlib import Path

from ..atomic import atomic_write_bytes
from .models import RuleSpec, MAX_RULES, validate_rule

MAX_JSON_BYTES = 2 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющийся JSON-ключ: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Недопустимая JSON-константа: {value}")


def save_rule_set(rules: list[RuleSpec], path: str | Path) -> Path:
    """Export accepted rules only. Candidates/rejected rules are deliberately omitted."""
    if not isinstance(rules, (list, tuple)) or len(rules) > MAX_RULES:
        raise ValueError("Ожидался набор до 500 правил.")
    exported = []
    ids = set()
    for rule in rules:
        if not isinstance(rule, RuleSpec):
            raise ValueError("Ожидался RuleSpec.")
        validate_rule(rule)
        if rule.review_state != "accepted":
            continue
        if rule.id in ids:
            raise ValueError("Набор содержит повторяющееся правило.")
        ids.add(rule.id)
        exported.append({"id": rule.id, "kind": rule.kind, "columns": list(rule.columns), "params": rule.params})
    exported.sort(key=lambda item: (item["kind"], item["columns"], item["id"]))
    payload = json.dumps({"schema": "data-xray-rule-set", "version": 2, "rules": exported},
                         ensure_ascii=False, indent=2, allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("Набор правил превышает 2 МиБ.")
    path = Path(path)
    atomic_write_bytes(path, (payload + "\n").encode("utf-8"))
    return path


def load_rule_set(path: str | Path) -> list[RuleSpec]:
    path = Path(path)
    with path.open("rb") as stream:
        payload = stream.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("Набор правил превышает 2 МиБ.")
    try:
        obj = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Некорректный JSON набора правил.") from exc
    if type(obj) is not dict or set(obj) != {"schema", "version", "rules"}:
        raise ValueError("Неизвестная структура набора правил.")
    if obj["schema"] != "data-xray-rule-set" or type(obj["version"]) is not int or obj["version"] not in (1, 2):
        raise ValueError("Неподдерживаемая схема или версия набора правил.")
    if type(obj["rules"]) is not list or len(obj["rules"]) > MAX_RULES:
        raise ValueError("В наборе допускается до 500 правил.")
    rules = []
    ids = set()
    for item in obj["rules"]:
        if type(item) is not dict or set(item) != {"id", "kind", "columns", "params"}:
            raise ValueError("Некорректная структура правила.")
        if type(item["columns"]) is not list:
            raise ValueError("columns должен быть массивом имен.")
        try:
            rule = RuleSpec(item["kind"], item["columns"], item["params"], "accepted")
        except (TypeError, OverflowError) as exc:
            raise ValueError("Некорректные параметры правила.") from exc
        if not isinstance(item["id"], str) or item["id"] != rule.id or rule.id in ids:
            raise ValueError("Идентификатор правила не совпадает с содержимым или повторяется.")
        ids.add(rule.id)
        rules.append(rule)
    return sorted(rules, key=lambda rule: (rule.kind, rule.columns, rule.id))
