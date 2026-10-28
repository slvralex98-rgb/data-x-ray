"""Public contracts for bounded, UI-independent rule discovery and checking."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any

KINDS = {"not_null", "allowed_values", "robust_range", "string_shape", "functional_dependency", "conditional_required", "comparison", "numeric_constraint", "date_format", "regex", "unique"}
MAX_UNIQUE_COLUMNS = 32
MAX_PATTERN = 512
MAX_RULES = 500
MAX_DOMAIN = 200
MAX_TEXT = 4096
DEFAULT_NULL_TOKENS = ("null", "none", "n/a", "na", "nan")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name}: требуется целое число от {low} до {high}.")


def _text(value):
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise ValueError("Ожидалась непустая строка длиной до 4096 символов.")


@dataclass(frozen=True)
class EvaluationOptions:
    example_rows: int = 8
    max_violation_rows: int = 1000
    null_tokens: tuple[str, ...] = DEFAULT_NULL_TOKENS

    def __post_init__(self):
        _integer(self.example_rows, 0, 50, "example_rows")
        _integer(self.max_violation_rows, 0, 1000, "max_violation_rows")
        if not isinstance(self.null_tokens, (tuple, list)) or len(self.null_tokens) > 50:
            raise ValueError("null_tokens: ожидается до 50 строк.")
        for token in self.null_tokens:
            _text(token)
        object.__setattr__(self, "null_tokens", tuple(t.strip().casefold() for t in self.null_tokens))


@dataclass(frozen=True)
class MiningOptions(EvaluationOptions):
    min_support: int = 20
    min_confidence: float = .95
    max_columns: int = 32
    max_pairs: int = 120
    max_rules: int = 100
    max_cardinality: int = 30
    max_category_ratio: float = .2
    min_group_size: int = 3
    iqr_multiplier: float = 1.5

    def __post_init__(self):
        super().__post_init__()
        for name, low, high in (("min_support", 1, 2_000_000), ("max_columns", 1, 500),
                                ("max_pairs", 0, 10000), ("max_rules", 1, MAX_RULES),
                                ("max_cardinality", 2, MAX_DOMAIN), ("min_group_size", 3, 2_000_000)):
            _integer(getattr(self, name), low, high, name)
        for name, low, high in (("min_confidence", .5, 1), ("max_category_ratio", 0, .5), ("iqr_multiplier", 0, 100)):
            value = getattr(self, name)
            if not _finite(value) or not low < value <= high:
                raise ValueError(f"{name}: недопустимое значение.")


@dataclass(frozen=True)
class RuleSpec:
    kind: str
    columns: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    review_state: str = "candidate"

    def __post_init__(self):
        validate_rule(self)
        object.__setattr__(self, "columns", tuple(self.columns))
        if self.kind == "unique":
            object.__setattr__(self, "columns", tuple(sorted(self.columns)))
        # Detach mutable caller-owned structures; validation is repeated at execution/export.
        object.__setattr__(self, "params", json.loads(json.dumps(self.params, ensure_ascii=False, allow_nan=False)))
        if self.kind == "allowed_values":
            self.params["values"].sort()

    @property
    def id(self) -> str:
        payload = json.dumps({"kind": self.kind, "columns": self.columns, "params": self.params},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    @property
    def description(self) -> str:
        a = self.columns[0]
        p = self.params
        if self.kind == "unique":
            fields = ", ".join(f"«{column}»" for column in self.columns)
            return (f"Заполненное значение {fields} должно быть уникальным. " if len(self.columns) == 1 else
                    f"Заполненная комбинация полей {fields} должна быть уникальной. ") + "Все строки повторяющейся группы — нарушения; строки с пропуском любого поля пропускаются."
        if self.kind == "not_null":
            return f"«{a}» должно быть заполнено (NULL, пустоты и маркеры считаются пропусками)."
        if self.kind == "allowed_values":
            return f"Заполненное «{a}» входит в сохраненный набор из {len(p['values'])} значений."
        if self.kind == "robust_range":
            return f"Заполненное «{a}» — конечное число в [{p['lower']}, {p['upper']}] включительно."
        if self.kind == "numeric_constraint":
            parts = []
            if p['lower'] is not None:
                parts.append(f"{'≥' if p['lower_inclusive'] else '>'} {p['lower']}")
            if p['upper'] is not None:
                parts.append(f"{'≤' if p['upper_inclusive'] else '<'} {p['upper']}")
            return f"Заполненное «{a}» — конечное число: " + " и ".join(parts) + "."
        if self.kind == "date_format":
            return f"Заполненное «{a}» — календарная дата в точном формате {p['format']}."
        if self.kind == "regex":
            return f"Заполненное «{a}» целиком соответствует регулярному выражению {p['pattern']!r}."
        if self.kind == "string_shape":
            return f"Заполненное «{a}» соответствует формату {p['format']}" + (f" {p['shape']!r} (серии букв/цифр произвольной длины)." if p['format'] == 'shape' else ".")
        b = self.columns[1]
        if self.kind == "functional_dependency":
            return f"«{a}» → «{b}»: заполненные пары соответствуют сохраненной карте; новый определитель — нарушение."
        if self.kind == "conditional_required":
            return f"Если «{a}» = {p['value']!r}, то «{b}» должно быть заполнено."
        return f"Заполненные «{a}» {p['operator']} «{b}» ({p['type']}); неразбираемые значения — нарушения."


def validate_rule(rule: RuleSpec) -> None:
    if not isinstance(rule.kind, str) or rule.kind not in KINDS:
        raise ValueError("Неизвестный вид правила.")
    if rule.review_state not in ("candidate", "accepted", "rejected"):
        raise ValueError("Неизвестное состояние рассмотрения правила.")
    count = 2 if rule.kind in ("functional_dependency", "conditional_required", "comparison") else 1
    if not isinstance(rule.columns, (tuple, list)) or not (1 <= len(rule.columns) <= MAX_UNIQUE_COLUMNS if rule.kind == "unique" else len(rule.columns) == count):
        raise ValueError("Неверное число столбцов правила.")
    for column in rule.columns:
        _text(column)
    if len(set(rule.columns)) != len(rule.columns):
        raise ValueError("Столбцы правила должны различаться.")
    if type(rule.params) is not dict:
        raise ValueError("params должен быть объектом.")
    p = rule.params
    fields = {"unique": set(), "not_null": set(), "allowed_values": {"values"}, "robust_range": {"lower", "upper"},
              "functional_dependency": {"mapping"}, "conditional_required": {"value"},
              "comparison": {"operator", "type"}, "string_shape": {"format", "shape"} if p.get("format") == "shape" else {"format"},
              "numeric_constraint": {"lower", "upper", "lower_inclusive", "upper_inclusive"},
              "date_format": {"format"}, "regex": {"pattern"}}
    if set(p) != fields[rule.kind]:
        raise ValueError("Неизвестные или отсутствующие параметры правила.")
    if rule.kind == "allowed_values":
        if type(p["values"]) is not list or not 1 <= len(p["values"]) <= MAX_DOMAIN:
            raise ValueError("Домен должен содержать от 1 до 200 значений.")
        for value in p["values"]:
            _text(value)
        if len(set(p["values"])) != len(p["values"]):
            raise ValueError("Повторяющиеся значения домена.")
    elif rule.kind == "robust_range":
        for key in ("lower", "upper"):
            if not _finite(p[key]):
                raise ValueError("Границы должны быть конечными числами.")
        if p["lower"] > p["upper"]:
            raise ValueError("Нижняя граница больше верхней.")
    elif rule.kind == "numeric_constraint":
        for key in ("lower", "upper"):
            if p[key] is not None and not _finite(p[key]):
                raise ValueError("Границы должны быть конечными числами или отсутствовать.")
        if any(type(p[key]) is not bool for key in ("lower_inclusive", "upper_inclusive")):
            raise ValueError("Включение границ задается логическим значением.")
        if p['lower'] is None and p['upper'] is None:
            raise ValueError("Укажите хотя бы одну границу.")
        if p['lower'] is not None and p['upper'] is not None:
            if p['lower'] > p['upper'] or p['lower'] == p['upper'] and not (p['lower_inclusive'] and p['upper_inclusive']):
                raise ValueError("Диапазон пуст: проверьте значения и включение границ.")
    elif rule.kind == "regex":
        validate_pattern(p['pattern'])
    elif rule.kind == "date_format":
        date_format_pattern(p['format'])
    elif rule.kind == "functional_dependency":
        if type(p["mapping"]) is not dict or not 2 <= len(p["mapping"]) <= MAX_DOMAIN:
            raise ValueError("Карта зависимости должна содержать от 2 до 200 пар.")
        for key, value in p["mapping"].items():
            _text(key)
            _text(value)
        if len(set(p["mapping"].values())) < 2:
            raise ValueError("Постоянная цель не является функциональной зависимостью.")
    elif rule.kind == "conditional_required":
        _text(p["value"])
    elif rule.kind == "comparison":
        if p["operator"] not in ("<=", ">=", "=") or p["type"] not in ("number", "date"):
            raise ValueError("Неизвестная операция или тип сравнения.")
    elif rule.kind == "string_shape":
        if p["format"] not in ("shape", "email", "uuid", "iso_date", "digits"):
            raise ValueError("Неизвестный безопасный формат.")
        if p["format"] == "shape":
            _text(p["shape"])


def validate_pattern(pattern: str) -> None:
    """Compile with the same bounded Rust engine used during execution."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
        raise ValueError(f"Регулярное выражение: от 1 до {MAX_PATTERN} символов.")
    import polars as pl
    try:
        pl.Series([""]).str.contains(r"\A(?:" + pattern + r")\z", strict=True)
    except pl.exceptions.PolarsError as exc:
        raise ValueError("Регулярное выражение не поддерживается Rust regex: проверьте синтаксис; ссылки назад и проверки вперед/назад недоступны.") from exc


def date_format_pattern(fmt: str) -> str:
    """Restricted strftime grammar: exact widths, no ambiguous/localized directives."""
    if not isinstance(fmt, str) or not 1 <= len(fmt) <= 64:
        raise ValueError("Формат даты должен содержать от 1 до 64 символов.")
    directives = {"Y": "[0-9]{4}", "m": "[0-9]{2}", "d": "[0-9]{2}",
                  "H": "[0-9]{2}", "M": "[0-9]{2}", "S": "[0-9]{2}"}
    used, parts, i = [], [], 0
    while i < len(fmt):
        char = fmt[i]
        if char == "%":
            i += 1
            if i >= len(fmt) or fmt[i] not in directives:
                raise ValueError("Допустимы только %Y, %m, %d и полное время %H:%M:%S.")
            used.append(fmt[i])
            parts.append(directives[fmt[i]])
        elif char in " -./:T":
            parts.append("\\." if char == "." else char)
        else:
            raise ValueError("Между частями даты допустимы пробел, T и знаки - . / :.")
        i += 1
    if len(used) != len(set(used)) or set(used) not in ({"Y", "m", "d"}, {"Y", "m", "d", "H", "M", "S"}):
        raise ValueError("Нужны %Y, %m, %d ровно по одному; время задается всеми %H, %M, %S.")
    return r"\A(?:" + "".join(parts) + r")\z"


@dataclass
class RuleEvaluation:
    rule: RuleSpec
    total_rows: int
    evaluated: int
    passed: int
    violations: int
    skipped: int
    confidence: float | None
    status: str
    row_indices: list[int] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class MiningResult:
    source: str
    rows: int
    evaluations: list[RuleEvaluation]
    notes: list[str]
    options: EvaluationOptions
    elapsed_seconds: float = 0

    @property
    def rules(self) -> list[RuleSpec]:
        return [evaluation.rule for evaluation in self.evaluations]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe report; special scalars become text, nonfinite floats explicit text."""
        def safe(value):
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else str(value)
            if isinstance(value, dict):
                return {str(key): safe(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [safe(item) for item in value]
            # datetime/date/Decimal and other scalar Parquet values are not code.
            return str(value)

        result = safe(asdict(self))
        for item, evaluation in zip(result["evaluations"], self.evaluations):
            item["rule"]["id"] = evaluation.rule.id
            item["rule"]["description"] = evaluation.rule.description
        return result
