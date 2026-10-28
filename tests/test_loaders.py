import csv

import polars as pl
import pytest
from openpyxl import Workbook

from data_xray.demo import create_demo
from data_xray.engine import Analyzer
from data_xray.loaders import load_dataset, excel_sheets


@pytest.mark.parametrize("extension", ["csv", "xlsx", "parquet"])
def test_real_format_roundtrip_and_truncation(tmp_path, extension):
    create_demo(tmp_path)
    dataset = load_dataset(tmp_path / f"demo.{extension}", max_rows=20)
    assert dataset.frame.shape == (20, 8)
    assert dataset.truncated
    result = Analyzer().analyze(dataset)
    assert result.truncated and "первые 20" in " ".join(result.notes)


def test_cp1251_semicolon_multiline_and_leading_zeros(tmp_path):
    path = tmp_path / "тест.csv"
    with path.open("w", encoding="cp1251", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["код", "текст"])
        writer.writerow(["001", "строка\nвторая"])
        writer.writerow(["002", "значение"])
    ds = load_dataset(path, encoding="cp1251")
    assert ds.frame["код"][0] == "001"
    assert ds.frame["текст"][0] == "строка\nвторая"
    assert not load_dataset(path, encoding="cp1251", max_rows=2).truncated


@pytest.mark.parametrize("contents", ["", "a,a\n1,2", "a,\n1,2", "a,b\n1,2,3", 'a,b\n"unterminated,2'])
def test_bad_csv_is_not_silently_repaired(tmp_path, contents):
    path = tmp_path / "bad.csv"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises((ValueError, csv.Error)):
        load_dataset(path, separator=",")


def test_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("a,b\n", encoding="utf-8")
    assert load_dataset(path).frame.shape == (0, 2)


def test_excel_sheet_and_formula_are_explicit(tmp_path):
    book = Workbook()
    book.active.title = "first"
    book.active.append(["a"])
    book.active.append(["first-value"])
    ws = book.create_sheet("second")
    ws.append(["b"])
    ws.append(["=1+1"])
    path = tmp_path / "book.xlsx"
    book.save(path)
    book.close()
    assert excel_sheets(path) == ["first", "second"]
    dataset = load_dataset(path, sheet="second")
    assert dataset.frame["b"][0] == "=1+1"
    assert "Формулы" in " ".join(dataset.notes)
    with pytest.raises(ValueError):
        load_dataset(path, sheet="missing")


def test_nested_parquet_is_rejected_with_explanation(tmp_path):
    path = tmp_path / "nested.parquet"
    pl.DataFrame({"x": [[1, 2]]}).write_parquet(path)
    with pytest.raises(ValueError, match="скалярные"):
        load_dataset(path)


def test_cell_budget_is_explicit_and_exact_limit_is_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr("data_xray.loaders.MAX_CELLS", 4)
    path = tmp_path / "budget.csv"
    path.write_text("a,b\n1,2\n3,4\n5,6", encoding="utf-8")
    ds = load_dataset(path, max_rows=100)
    assert ds.frame.shape == (2, 2) and ds.truncated
    assert "первые 2" in " ".join(ds.notes)
    assert "4 ячеек" in " ".join(ds.notes)
    path.write_text("a,b\n1,2\n3,4", encoding="utf-8")
    assert not load_dataset(path).truncated


def test_auto_separator_keeps_malformed_semicolon_row_visible(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a;b\n1;2\n3;4;5", encoding="utf-8")
    with pytest.raises(ValueError, match="полей"):
        load_dataset(path)
