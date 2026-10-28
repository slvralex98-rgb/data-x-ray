from datetime import datetime

import polars as pl
import pytest

from data_xray.engine import Analyzer
from data_xray.loaders import Dataset
from data_xray.semantics import date_value


@pytest.mark.parametrize("value", [
    "0001-01-01T00:00:00+12:00",
    "9999-12-31T23:59:59-12:00",
])
def test_dates_outside_representable_utc_are_unparseable(value):
    assert date_value(value) is None
    assert date_value(datetime.fromisoformat(value)) is None


@pytest.mark.parametrize("value,expected", [
    ("0001-01-01T12:00:00+12:00", datetime(1, 1, 1)),
    ("9999-12-31T11:59:59-12:00", datetime(9999, 12, 31, 23, 59, 59)),
    ("2024-02-29T01:00:00+01:00", datetime(2024, 2, 29)),
    ("2024/02/29", datetime(2024, 2, 29)),
    ("29.02.2024", datetime(2024, 2, 29)),
    ("29/02/2024", datetime(2024, 2, 29)),
    ("31.02.2024", None),
    ("not a date", None),
    ("2023-02-29", None),
])
def test_date_conversion_preserves_utc_boundaries(value, expected):
    assert date_value(value) == expected


def test_analysis_reports_utc_overflow_as_invalid_date():
    values = ["2024-01-01"] * 20 + ["0001-01-01T00:00:00+12:00"]
    result = Analyzer().analyze(Dataset(pl.DataFrame({"date": values}), "memory", "csv", False, []))
    invalid = next(f for f in result.findings if f.code == "date_invalid")
    assert invalid.row_indices == [20]
    assert invalid.examples[0]["values"]["date"] == values[-1]
