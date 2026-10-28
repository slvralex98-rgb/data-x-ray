"""Exact numeric comparisons for explicit rules; heuristic parsing stays separate."""
from decimal import Decimal, InvalidOperation
import math

from ..semantics import NUMBER


def exact_numeric(value):
    text = str(value).strip().replace(',', '.')
    if not NUMBER.fullmatch(text):
        return None
    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def numeric_bound(text):
    """Keep integral UI bounds exact within the existing JSON number contract."""
    if not text.strip():
        return None
    number = exact_numeric(text)
    if number is None or not math.isfinite(float(number)):
        raise ValueError('Граница должна быть конечным числом.')
    if number == number.to_integral_value():
        return int(number)
    result = float(number)
    if exact_numeric(result) != number:
        raise ValueError('Граница содержит слишком много значащих цифр; сократите дробную часть.')
    return result
