"""Conservative, locale-explicit parsing for heuristic type discovery."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any

NUMBER = re.compile(r"^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d+)?$")
DATE_SHAPE = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{4})(?:[ T].*)?$")


def numeric(value: Any) -> float | None:
    s = str(value).strip()
    if not NUMBER.fullmatch(s):
        return None
    try:
        n = float(s.replace(",", "."))
        return n if math.isfinite(n) else None
    except (ValueError, OverflowError):
        return None


def _date_text(text: str) -> datetime | None:
    if not DATE_SHAPE.fullmatch(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def date_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        result = _date_text(str(value).strip())
    if result is None:
        return None
    try:
        return result.astimezone(timezone.utc).replace(tzinfo=None) if result.tzinfo else result
    except OverflowError:
        return None


def missing_kind(value: Any, tokens: tuple[str, ...]) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    s = str(value).strip()
    if not s:
        return "blank"
    if s.casefold() in tokens:
        return "marker"
    return None


def leading_zero(value: Any) -> bool:
    return bool(re.fullmatch(r"0\d+", str(value).strip()))


def shape(value: Any) -> str:
    s = str(value)
    return re.sub(r"(.)\1+", r"\1", "".join("A" if c.isalpha() else "9" if c.isdigit() else c for c in s))
