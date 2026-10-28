"""Local review flags and strictly validated, inert analysis snapshots.

Snapshots are reports, never a source of rows for the active dataset.
"""
from __future__ import annotations

from dataclasses import asdict, fields
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path

from .atomic import atomic_write_bytes
from .models import AnalysisOptions, AnalysisResult, ColumnProfile, Finding

STATES = {"unreviewed": "Не разобрано", "confirmed": "Подтверждено",
          "not_problem": "Не проблема", "fixed": "Исправлено"}
FORMAT = "data-xray-review"
VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
MAX_COMMENT = 10_000


def _plain(value):
    """Preserve report values; native temporal/decimal/binary cells become text."""
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Ключи снимка должны быть строками.")
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise ValueError("Неподдерживаемое значение в снимке анализа.")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def analysis_fingerprint(result: AnalysisResult) -> str:
    data = _plain(result.to_dict())
    data.pop("created_at")
    data.pop("elapsed_seconds")
    return sha256(_canonical(data).encode("utf-8")).hexdigest()


def finding_id(finding: Finding) -> str:
    return sha256(_canonical([finding.code, finding.columns, finding.title]).encode("utf-8")).hexdigest()


class FindingReview:
    """One analysis' annotations. All mutations leave findings and table intact."""
    def __init__(self, result: AnalysisResult):
        self.fingerprint = analysis_fingerprint(result)
        base = _plain(result.to_dict())
        for key in ("created_at", "elapsed_seconds", "findings"):
            base.pop(key)
        self._base_fingerprint = sha256(_canonical(base).encode("utf-8")).hexdigest()
        self._findings = {finding_id(f): f for f in result.findings}
        if len(self._findings) != len(result.findings):
            raise ValueError("Находки имеют неоднозначные идентификаторы.")
        # Keep immutable evidence digests: hosts may replace findings on the
        # same mutable AnalysisResult, or edit an existing Finding in place.
        self._evidence = {key: sha256(_canonical(_plain(asdict(f))).encode("utf-8")).hexdigest()
                          for key, f in self._findings.items()}
        self.states = {key: {"state": "unreviewed", "comment": "", "deleted": False}
                       for key in self._findings}

    @property
    def review(self):
        return self.states

    def matches(self, result):
        return self.fingerprint == analysis_fingerprint(result)

    def rebase(self, result):
        """Carry flags only where the entire finding, including evidence, is equal."""
        updated = FindingReview(result)
        if self._base_fingerprint != updated._base_fingerprint:
            return updated
        for key in updated._findings:
            if key in self._evidence and self._evidence[key] == updated._evidence[key]:
                updated.states[key] = dict(self.states[key])
        return updated

    def _key(self, finding):
        key = finding_id(finding)
        if key not in self._findings or finding != self._findings[key]:
            raise ValueError("Находка не принадлежит этому анализу.")
        return key

    def get_state(self, finding):
        return dict(self.states[self._key(finding)])

    def mark(self, finding, state, comment=None):
        if state not in STATES:
            raise ValueError("Неизвестный статус проблемы.")
        key = self._key(finding)
        if comment is not None:
            _string(comment, MAX_COMMENT)
            self.states[key]["comment"] = comment
        self.states[key]["state"] = state

    def remove(self, finding):
        self.states[self._key(finding)]["deleted"] = True

    def restore(self, finding):
        self.states[self._key(finding)]["deleted"] = False

    def visible(self, filter="active", findings=None):
        if filter not in ("active", "all", "deleted", *STATES):
            raise ValueError("Неизвестный фильтр обзора.")
        items = self._findings.values() if findings is None else findings
        answer = []
        for finding in items:
            state = self.get_state(finding)
            if filter == "all" or (filter == "deleted" and state["deleted"]) or (
                    not state["deleted"] and (filter == "active" or state["state"] == filter)):
                answer.append(finding)
        return answer

    def badge(self, finding):
        state = self.get_state(finding)
        return ("Удалено · " if state["deleted"] else "") + STATES[state["state"]]

    def detail_html(self, finding):
        state = self.get_state(finding)
        note = escape(state["comment"], quote=True).replace("\n", "<br>")
        return f'<p><strong>Обзор: {escape(self.badge(finding))}</strong></p>' + (
            f"<p>Комментарий: {note}</p>" if note else "")


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Неожиданные или отсутствующие поля снимка.")


def _string(value, limit=1_000_000):
    if type(value) is not str or len(value) > limit or "\x00" in value:
        raise ValueError("Недопустимая строка снимка.")


def _integer(value, high=10_000_000):
    if type(value) is not int or not 0 <= value <= high:
        raise ValueError("Недопустимое целое число снимка.")


def _number(value, low=0, high=1e308):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Недопустимое число снимка.")


def _list(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("Превышен размер списка снимка.")


def _tree(value, depth=0, budget=None):
    if budget is None:
        budget = [3_000_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 20:
        raise ValueError("Снимок слишком сложный.")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _string(value)
    elif type(value) in (int, float):
        if abs(value) > 1e308 or not math.isfinite(value):
            raise ValueError("Нечисловое или слишком большое значение снимка.")
    elif type(value) is list:
        _list(value, 10_000_000)
        for item in value:
            _tree(item, depth + 1, budget)
    elif type(value) is dict:
        if len(value) > 10_000:
            raise ValueError("Слишком много полей снимка.")
        for key, item in value.items():
            _string(key)
            _tree(item, depth + 1, budget)
    else:
        raise ValueError("Недопустимый тип снимка.")


def _analysis(data):
    _object(data, [f.name for f in fields(AnalysisResult)])
    for key in ("source", "source_format", "created_at"):
        _string(data[key])
    _integer(data["rows"])
    _integer(data["columns"], 10_000)
    _number(data["elapsed_seconds"])
    if type(data["truncated"]) is not bool:
        raise ValueError("Недопустимый признак фрагмента.")
    opts = data["options"]
    _object(opts, [f.name for f in fields(AnalysisOptions)])
    for key in ("max_rows", "example_rows", "max_key_columns"):
        _integer(opts[key])
        if opts[key] == 0:
            raise ValueError("Параметры анализа должны быть положительными.")
    _integer(opts["example_rows"], 1000)
    _number(opts["dominance"], .500000000000001, 1)
    _number(opts["iqr_multiplier"], 1e-300, 1e6)
    _list(opts["null_tokens"], 1000)
    for token in opts["null_tokens"]:
        _string(token, 10_000)
    _list(data["notes"], 10_000)
    for note in data["notes"]:
        _string(note)
    _list(data["profiles"], 10_000)
    if len(data["profiles"]) != data["columns"]:
        raise ValueError("Число профилей не совпадает с числом столбцов.")
    names = []
    profiles = []
    for p in data["profiles"]:
        _object(p, [f.name for f in fields(ColumnProfile)])
        for key in ("name", "physical_type", "inferred_type"):
            _string(p[key])
        if p["inferred_type"] not in ("number", "date", "string", "boolean", "empty"):
            raise ValueError("Неизвестный тип столбца.")
        names.append(p["name"])
        for key in ("missing", "nulls", "blanks", "markers", "distinct"):
            _integer(p[key], data["rows"])
        if p["missing"] != p["nulls"] + p["blanks"] + p["markers"]:
            raise ValueError("Некорректные счетчики пропусков.")
        _number(p["unique_ratio"], 0, 1)
        if type(p["statistics"]) is not dict:
            raise ValueError("Некорректная статистика.")
        _list(p["top_values"], 1000)
        for entry in p["top_values"]:
            _list(entry, 2)
            if len(entry) != 2:
                raise ValueError("Некорректное частое значение.")
            _string(entry[0])
            _integer(entry[1], data["rows"])
        profiles.append(ColumnProfile(**{**p, "top_values": [tuple(v) for v in p["top_values"]]}))
    if len(names) != len(set(names)):
        raise ValueError("Повторяющиеся имена столбцов.")
    _list(data["findings"], 50_000)
    findings = []
    for f in data["findings"]:
        _object(f, [field.name for field in fields(Finding)])
        for key in ("code", "title", "severity", "confidence", "description", "recommendation"):
            _string(f[key])
        if f["severity"] not in ("high", "medium", "low", "info") or f["confidence"] not in ("fact", "heuristic"):
            raise ValueError("Неизвестная категория находки.")
        _list(f["columns"], 10_000)
        for name in f["columns"]:
            _string(name)
            if name not in names:
                raise ValueError("Находка ссылается на неизвестный столбец.")
        _integer(f["total"], data["rows"])
        _integer(f["count"], f["total"])
        _integer(f["score"], 1000)
        _list(f["row_indices"], data["rows"])
        for index in f["row_indices"]:
            _integer(index, max(0, data["rows"] - 1))
            if data["rows"] == 0:
                raise ValueError("В пустой таблице нет записей.")
        if len(set(f["row_indices"])) != len(f["row_indices"]):
            raise ValueError("Повторяющиеся номера записей.")
        _list(f["examples"], 1000)
        for example in f["examples"]:
            _object(example, ("row", "values"))
            _integer(example["row"], data["rows"])
            if example["row"] < 1 or type(example["values"]) is not dict or set(example["values"]) != set(f["columns"]):
                raise ValueError("Некорректный пример находки.")
        findings.append(Finding(**f))
    return AnalysisResult(**{**data, "profiles": profiles, "findings": findings,
                            "options": AnalysisOptions(**{**opts, "null_tokens": tuple(opts["null_tokens"])})})


def _session(data):
    _tree(data)
    _object(data, ("format", "version", "fingerprint", "analysis", "review"))
    if data["format"] != FORMAT or type(data["version"]) is not int or data["version"] != VERSION:
        raise ValueError("Неизвестный формат или версия сохраненного обзора.")
    result = _analysis(data["analysis"])
    review = FindingReview(result)
    if data["fingerprint"] != review.fingerprint:
        raise ValueError("Идентификатор анализа не совпадает с содержимым снимка.")
    _object(data["review"], review.states)
    for key, state in data["review"].items():
        _object(state, ("state", "comment", "deleted"))
        if type(state["state"]) is not str or state["state"] not in STATES or type(state["deleted"]) is not bool:
            raise ValueError("Некорректная отметка проблемы.")
        _string(state["comment"], MAX_COMMENT)
        review.states[key] = dict(state)
    return result, review


def save_review_session(result, review, path):
    """Save the entire snapshot, including hidden/deleted findings, after validation."""
    if not review.matches(result):
        raise ValueError("Отметки относятся к другому анализу.")
    data = {"format": FORMAT, "version": VERSION, "fingerprint": review.fingerprint,
            "analysis": _plain(result.to_dict()), "review": review.states}
    _session(data)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("Снимок превышает лимит 64 МиБ.")
    atomic_write_bytes(path, payload)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющееся поле JSON.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("NaN и Infinity в JSON запрещены.")


def load_review_session(path):
    """Return an isolated report and annotations; never touch an active dataset."""
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Снимок превышает лимит 64 МиБ.")
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_invalid_constant)
        return _session(data)
    except (UnicodeError, RecursionError, OverflowError, TypeError, KeyError) as exc:
        raise ValueError("Некорректный сохраненный обзор.") from exc


def render_review_html(result, review, *, marked_only=False):
    """Escaped read-only report; no drilldown URLs, scripts or active data changes."""
    from .report import STYLE, finding_html
    if not review.matches(result):
        raise ValueError("Отметки относятся к другому анализу.")
    findings = result.findings
    if marked_only:
        findings = [f for f in findings if review.get_state(f)["state"] != "unreviewed"]
    cards = "".join(review.detail_html(f) + finding_html(f) for f in findings)
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Сохраненный обзор</title>'
            f'<style>{STYLE}</style></head><body><main><h1>Сохраненный обзор</h1>'
            '<p>Снимок результатов анализа и отметок. Данные не изменены. '
            'Номера записей относятся только к сохраненному анализу. Это не набор правил для следующей таблицы.</p>'
            f'<p>{escape(result.source)} · {result.rows:,} записей · {result.columns} столбцов</p>'
            f'<p>Идентификатор анализа: {escape(review.fingerprint)}</p>{cards or "<p>Нет отмеченных проблем.</p>"}'
            '</main></body></html>')


def export_marked_html(result, review, path):
    Path(path).write_text(render_review_html(result, review, marked_only=True), encoding="utf-8")
