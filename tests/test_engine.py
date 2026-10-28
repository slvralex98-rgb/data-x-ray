from threading import Event

import polars as pl
import pytest

from data_xray.checks import AnalysisCancelled
from data_xray.demo import create_demo
from data_xray.engine import Analyzer
from data_xray.loaders import Dataset, load_dataset
from data_xray.models import AnalysisOptions
from data_xray.report import render_report


def analyze(data):
    return Analyzer().analyze(Dataset(pl.DataFrame(data), "test.parquet", "parquet", False, []))


def test_demo_has_expected_findings_and_exact_examples(tmp_path):
    result = Analyzer().analyze(load_dataset(create_demo(tmp_path)))
    codes = {f.code for f in result.findings}
    assert {"missing", "duplicates", "type", "outlier", "date_invalid", "date_format", "date_future", "whitespace", "control", "variants", "string_pattern", "string_length"} <= codes
    assert (result.rows, result.columns) == (121, 8)
    duplicate = next(f for f in result.findings if f.code == "duplicates")
    assert duplicate.count == 2
    assert [e["row"] for e in duplicate.examples] == [1, 121]
    assert "лишних копий: 1" in duplicate.description
    invalid = next(f for f in result.findings if f.code == "type")
    assert invalid.examples[0]["values"]["amount"] == "twelve"
    assert invalid.examples[0]["row"] == 7
    assert [f.score for f in result.findings] == sorted([f.score for f in result.findings], reverse=True)
    assert not any(f.code in ("key", "composite_key") for f in result.findings)


def test_null_categories_and_nonfinite_are_distinct():
    result = analyze({"x": [None, "", "  ", "NULL", "N/A", "value"]})
    p = result.profiles[0]
    assert (p.nulls, p.blanks, p.markers, p.missing, p.distinct) == (1, 2, 2, 5, 1)
    result = analyze({"x": [float("nan"), float("inf"), 1.0]})
    assert result.profiles[0].missing == 0
    assert next(f for f in result.findings if f.code == "nonfinite").count == 2
    assert not any(f.code in ("key", "composite_key") for f in result.findings)


def test_keys_single_and_minimal_pairs():
    result = analyze({"id": [1, 2, 3, 4], "a": [1, 1, 2, 2], "b": [1, 2, 1, 2], "nullable": [None, 2, 3, 4]})
    keys = [f.columns for f in result.findings if f.code == "key"]
    assert keys == [["id"]]
    assert [f.columns for f in result.findings if f.code == "composite_key"] == [["a", "b"]]


def test_empty_single_row_constant_and_iqr_zero():
    empty = analyze({"x": pl.Series([], dtype=pl.String)})
    assert empty.rows == 0 and not empty.findings
    one = analyze({"x": ["a"]})
    assert not any(f.code == "key" for f in one.findings)
    result = analyze({"x": [0] * 99 + [100]})
    assert next(f for f in result.findings if f.code == "numeric_rare").row_indices == [99]


def test_zero_padded_codes_and_decimal_comma():
    result = analyze({"code": [f"{i:04}" for i in range(20)], "n": ["1,25"] * 19 + ["oops"]})
    assert result.profiles[0].inferred_type == "string"
    assert result.profiles[1].statistics["mean"] == 1.25
    assert next(f for f in result.findings if f.code == "type").row_indices == [19]


def test_dates_calendar_timezone_and_formats():
    result = analyze({"when": ["2025-01-01T23:00:00+03:00"] * 8 + ["31.02.2025", "02/03/2025"]})
    assert result.profiles[0].inferred_type == "date"
    assert next(f for f in result.findings if f.code == "date_invalid").row_indices == [8]
    assert result.profiles[0].statistics["min"] == "2025-01-01T20:00:00"


def test_report_escapes_untrusted_headers_values_and_path():
    result = analyze({"<img src=x onerror=alert(1)>": ["<script>alert(1)</script>"] * 10})
    result.source = "<script>source</script>.csv"
    html = render_report(result)
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "https://" not in html


def test_cancel_and_custom_detector():
    dataset = Dataset(pl.DataFrame({"x": [1]}), "x", "csv", False, [])
    cancel = Event()
    cancel.set()
    with pytest.raises(AnalysisCancelled):
        Analyzer().analyze(dataset, cancel=cancel)
    assert Analyzer(detectors=[]).analyze(dataset).findings == []


def test_result_limits_example_and_affected_rows():
    result = analyze({"x": [None] * 1500})
    f = next(f for f in result.findings if f.code == "missing")
    assert f.count == 1500 and len(f.row_indices) == 1000 and len(f.examples) == 8
