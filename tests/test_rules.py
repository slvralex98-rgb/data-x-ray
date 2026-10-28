from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from threading import Event

import polars as pl
import pytest

from data_xray.checks import AnalysisCancelled
from data_xray.loaders import Dataset, load_dataset
from data_xray.rules import (EvaluationOptions, MiningOptions, RuleSpec, RuleMiner,
                             evaluate_rules, save_rule_set, load_rule_set)
from data_xray.rules.demo import create_rule_demo
from data_xray.rules.storage import MAX_JSON_BYTES


def ds(data):
    return Dataset(pl.DataFrame(data, strict=False), "memory", "parquet", False, [])


def check(data, kind, columns, params=None, options=None):
    return evaluate_rules(ds(data), [RuleSpec(kind, tuple(columns), params or {})], options).evaluations[0]


def test_not_null_counts_tokens_and_positions():
    result = check({"x": [None, "", "  ", "NULL", "N/A", "00", "ok"]}, "not_null", ["x"])
    assert (result.total_rows, result.evaluated, result.passed, result.violations, result.skipped) == (7, 7, 2, 5, 0)
    assert result.confidence == 2 / 7
    assert result.row_indices == [0, 1, 2, 3, 4]
    assert [example["row"] for example in result.examples] == [1, 2, 3, 4, 5]
    custom = check({"x": ["MISSING", "NA"]}, "not_null", ["x"], options=EvaluationOptions(null_tokens=("MISSING",)))
    assert custom.row_indices == [0]


def test_domain_preserves_leading_zeroes_and_exact_values():
    result = check({"x": ["001", "1", " 001", None, ""]}, "allowed_values", ["x"], {"values": ["001"]})
    assert (result.evaluated, result.violations, result.skipped) == (3, 2, 2)
    assert result.row_indices == [1, 2]


def test_ranges_invalid_numbers_missing_nonfinite_and_comma():
    result = check({"x": ["1,25", "2.5", "oops", "inf", "1e999", None, "", "NULL", "5"]},
                   "robust_range", ["x"], {"lower": 1, "upper": 3})
    assert (result.evaluated, result.passed, result.violations, result.skipped) == (6, 2, 4, 3)
    assert result.row_indices == [2, 3, 4, 8]
    assert result.evidence["invalid_rows"] == 3
    native = check({"x": [float("nan"), float("inf"), 1.]}, "robust_range", ["x"], {"lower": 0, "upper": 2})
    assert native.evaluated == 3 and native.violations == 2 and native.skipped == 0


@pytest.mark.parametrize("fmt,values,bad", [
    ("email", ["a@b.co", "bad", "x @b.co"], [1, 2]),
    ("uuid", ["12345678-1234-1234-1234-123456789abc", "xyz"], [1]),
    ("iso_date", ["2024-02-29", "2023-02-29", "2024/02/29"], [1, 2]),
    ("digits", ["001", "１２３", "1.2"], [1, 2]),
])
def test_named_safe_string_formats(fmt, values, bad):
    result = check({"x": values}, "string_shape", ["x"], {"format": fmt})
    assert result.row_indices == bad


def test_shape_uses_variable_runs_and_literal_punctuation():
    result = check({"x": ["AB-001", "X-09", "X_09", ""]}, "string_shape", ["x"], {"format": "shape", "shape": "A-9"})
    assert result.row_indices == [2] and result.skipped == 1


def test_conditional_denominator_and_missing_target():
    result = check({"state": ["closed", "open", "closed", None, "closed"], "date": ["x", "", "NA", "", None]},
                   "conditional_required", ["state", "date"], {"value": "closed"})
    assert (result.evaluated, result.passed, result.violations, result.skipped) == (3, 1, 2, 2)
    assert result.row_indices == [2, 4] and result.evidence["condition_not_met"] == 2


def test_fd_unseen_determinant_is_a_violation_not_silent_skip():
    result = check({"a": ["001", "001", "002", "003", "002", None], "b": ["x", "y", "y", "z", None, "x"]},
                   "functional_dependency", ["a", "b"], {"mapping": {"001": "x", "002": "y"}})
    assert (result.evaluated, result.passed, result.violations, result.skipped) == (4, 2, 2, 2)
    assert result.row_indices == [1, 3]
    assert result.examples[1]["reason"] == "unseen_determinant"
    assert result.evidence["unseen_determinants"] == 1


@pytest.mark.parametrize("operator,expected", [("<=", [1]), (">=", [0]), ("=", [0, 1])])
def test_numeric_comparison_operators(operator, expected):
    result = check({"a": [1, 3, 2], "b": [2, 2, 2]}, "comparison", ["a", "b"], {"operator": operator, "type": "number"})
    assert result.row_indices == expected


def test_dates_calendar_timezone_invalid_and_missing():
    result = check({"start": ["2026-01-01T02:00:00+02:00", "31.12.2025", "2026-02-30", "", "2026-01-03"],
                    "end": ["2026-01-01T00:00:00Z", "2026/01/01", "2026-03-01", "bad", "2026-01-02"]},
                   "comparison", ["start", "end"], {"operator": "<=", "type": "date"})
    assert (result.evaluated, result.passed, result.violations, result.skipped) == (4, 2, 2, 1)
    assert result.row_indices == [2, 4] and result.evidence["invalid_rows"] == 1
    native = check({"a": [date(2026, 1, 1)], "b": [date(2026, 1, 2)]}, "comparison", ["a", "b"], {"operator": "<=", "type": "date"})
    assert native.status == "pass"


def test_zero_applicable_and_missing_columns_are_not_pass():
    empty = check({"x": [None, "", "NA"]}, "robust_range", ["x"], {"lower": 1, "upper": 2})
    assert empty.status == "not_applicable" and empty.confidence is None and empty.evaluated == 0
    absent = check({"x": [1, 2]}, "not_null", ["y"])
    assert absent.status == "not_applicable" and absent.skipped == 2 and absent.message
    assert absent.evidence["missing_columns"] == ["y"]
    no_rows = check({"x": pl.Series([], dtype=pl.String)}, "not_null", ["x"])
    assert no_rows.status == "not_applicable" and no_rows.total_rows == 0


def test_evidence_caps_counts_still_cover_all_violations():
    result = check({"x": [None] * 1400}, "not_null", ["x"])
    assert result.violations == 1400 and len(result.row_indices) == 1000 and len(result.examples) == 8
    configured = check({"x": [None] * 10}, "not_null", ["x"], options=EvaluationOptions(example_rows=2, max_violation_rows=1))
    assert len(configured.examples) == 2 and configured.row_indices == [0]


def test_support_confidence_are_on_applicable_rows():
    tiny = RuleMiner().mine(ds({"x": ["a"] * 19}))
    assert tiny.evaluations == []
    records = {"a": ["closed"] * 20 + ["open"] * 80, "b": ["yes"] * 19 + [""] + [""] * 80}
    result = RuleMiner().mine(ds(records))
    conditional = next(ev for ev in result.evaluations if ev.rule.kind == "conditional_required" and ev.rule.params["value"] == "closed")
    assert conditional.evaluated == 20 and conditional.confidence == .95 and conditional.skipped == 80
    stronger = RuleMiner().mine(ds(records), options=MiningOptions(min_confidence=.96))
    assert not any(ev.rule.kind == "conditional_required" for ev in stronger.evaluations)


def test_fd_dominant_groups_not_unique_ids_or_constant_targets():
    data = {"category": ["a"] * 30 + ["b"] * 30 + ["c"] * 30,
            "region": ["north"] * 30 + ["west"] * 30 + ["south"] * 30,
            "id": [f"{i:04d}" for i in range(90)], "constant": ["fixed"] * 90}
    data["region"][8] = "west"
    result = RuleMiner().mine(ds(data))
    fd = next(ev for ev in result.evaluations if ev.rule.kind == "functional_dependency" and ev.rule.columns == ("category", "region"))
    assert fd.row_indices == [8] and fd.evaluated == 90
    assert not any(ev.rule.kind == "functional_dependency" and ("id" in ev.rule.columns or ev.rule.columns[1] == "constant") for ev in result.evaluations)


def test_fd_ties_small_groups_and_single_mapping_are_suppressed():
    tied = ds({"a": ["a"] * 20 + ["b"] * 20, "b": ["x", "y"] * 20})
    result = RuleMiner().mine(tied)
    assert not any(ev.rule.kind == "functional_dependency" and ev.rule.columns == ("a", "b") for ev in result.evaluations)
    small_groups = ds({"a": [str(i // 2) for i in range(100)], "b": [str(i // 2) for i in range(100)]})
    result = RuleMiner().mine(small_groups, options=MiningOptions(max_category_ratio=.5, max_cardinality=100))
    assert not any(ev.rule.kind == "functional_dependency" for ev in result.evaluations)


def test_ranges_shapes_comparisons_and_stable_order():
    data = {"code": [f"{i:04d}" for i in range(99)] + ["bad-code"], "amount": [str(100 + i % 10) for i in range(99)] + ["9000"],
            "start": [(date(2026, 1, i % 20 + 1)).isoformat() for i in range(100)],
            "end": [(date(2026, 2, i % 20 + 1)).isoformat() for i in range(100)]}
    first = RuleMiner().mine(ds(data))
    rules = {(ev.rule.kind, ev.rule.columns): ev for ev in first.evaluations}
    assert rules["robust_range", ("amount",)].row_indices == [99]
    assert ("robust_range", ("code",)) not in rules
    assert rules["string_shape", ("code",)].row_indices == [99]
    relation = rules["comparison", ("end", "start")]
    assert relation.rule.params == {"operator": ">=", "type": "date"}
    second = RuleMiner().mine(ds(dict(reversed(list(data.items())))))
    assert [ev.rule.id for ev in first.evaluations] == [ev.rule.id for ev in second.evaluations]


def test_budgets_and_cancel():
    dataset = ds({"a": list(range(100)), "b": list(range(100)), "c": list(range(100))})
    result = RuleMiner().mine(dataset, options=MiningOptions(max_columns=2, max_pairs=0, max_rules=1))
    assert len(result.evaluations) <= 1
    assert any("Бюджет столбцов" in note for note in result.notes)
    assert any("лимит" in note for note in result.notes)
    result = RuleMiner().mine(dataset, options=MiningOptions(max_pairs=1))
    assert any("проверено 1 из 3" in note for note in result.notes)
    cancel = Event()
    cancel.set()
    with pytest.raises(AnalysisCancelled):
        RuleMiner().mine(dataset, cancel=cancel)
    with pytest.raises(AnalysisCancelled):
        evaluate_rules(dataset, [], cancel=cancel)
    cancel.clear()
    with pytest.raises(AnalysisCancelled):
        RuleMiner().mine(dataset, cancel=cancel, progress=lambda *_: cancel.set())


@pytest.mark.parametrize("kwargs", [{"min_support": 0}, {"min_support": True}, {"min_confidence": .4},
    {"min_confidence": float("nan")}, {"iqr_multiplier": float("inf")}, {"max_category_ratio": .9},
    {"max_cardinality": 1}, {"max_pairs": -1}, {"min_group_size": 2}, {"example_rows": 51}, {"max_rules": 501}])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        MiningOptions(**kwargs)


@pytest.mark.parametrize("kind,columns,params", [
    ("eval", ["x"], {}), ("not_null", ["x"], {"eval": "evil()"}),
    ("string_shape", ["x"], {"format": "regex", "pattern": ".*"}),
    ("robust_range", ["x"], {"lower": float("nan"), "upper": 2}),
    ("robust_range", ["x"], {"lower": 3, "upper": 2}),
    ("comparison", ["x", "x"], {"operator": "<=", "type": "number"}),
    ("comparison", ["x", "y"], {"operator": "exec", "type": "number"}),
    ("functional_dependency", ["x", "y"], {"mapping": {"a": "x", "b": "x"}}),
    ("allowed_values", ["x"], {"values": [1, 2]}),
])
def test_invalid_specs(kind, columns, params):
    with pytest.raises(ValueError):
        RuleSpec(kind, columns, params)


def test_json_roundtrip_accepted_only_and_deterministic_id(tmp_path):
    accepted = RuleSpec("functional_dependency", ("group", "region"), {"mapping": {"b": "west", "a": "north"}}, "accepted")
    reordered = RuleSpec("functional_dependency", ("group", "region"), {"mapping": {"a": "north", "b": "west"}})
    assert accepted.id == reordered.id
    path = save_rule_set([RuleSpec("not_null", ("id",)), accepted], tmp_path / "rules.json")
    loaded = load_rule_set(path)
    assert loaded == [accepted]
    assert loaded[0].review_state == "accepted"
    assert "eval" not in path.read_text(encoding="utf-8")
    assert save_rule_set([], tmp_path / "empty.json")
    assert load_rule_set(tmp_path / "empty.json") == []


@pytest.mark.parametrize("mutate", [
    lambda obj: obj.update(version=3), lambda obj: obj.update(version=True),
    lambda obj: obj.update(sql="drop table"), lambda obj: obj.update(rules={}),
    lambda obj: obj["rules"][0].update(kind="eval"),
    lambda obj: obj["rules"][0].update(columns="x"),
    lambda obj: obj["rules"][0].update(params={"code": "__import__('os')"}),
    lambda obj: obj["rules"][0].update(id="tampered"),
    lambda obj: obj["rules"].append(obj["rules"][0]),
])
def test_json_rejects_schema_tampering_and_code(tmp_path, mutate):
    path = save_rule_set([RuleSpec("not_null", ("x",), review_state="accepted")], tmp_path / "rules.json")
    obj = json.loads(path.read_text(encoding="utf-8"))
    mutate(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rule_set(path)


def test_json_duplicate_keys_nonfinite_size_and_nesting(tmp_path):
    path = tmp_path / "rules.json"
    for text in ('{"version":1,"version":1}', '{"x": NaN}', '[' * 1500 + ']' * 1500):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_rule_set(path)
    path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError):
        load_rule_set(path)


def test_demo_roundtrip_and_changed_load(tmp_path):
    path = create_rule_demo(tmp_path)
    mined = RuleMiner().mine(load_dataset(path))
    assert mined.rows == 150
    by_kind = {(ev.rule.kind, ev.rule.columns): ev for ev in mined.evaluations}
    assert by_kind["functional_dependency", ("category", "region")].row_indices == [6, 26]
    assert by_kind["conditional_required", ("status", "closed_at")].row_indices == [10]
    assert by_kind["comparison", ("end_date", "start_date")].row_indices == [14]
    assert by_kind["robust_range", ("amount",)].row_indices == [18]
    assert by_kind["string_shape", ("contact",)].row_indices == [22]
    assert by_kind["allowed_values", ("category",)].row_indices == [26]
    accepted = [replace(ev.rule, review_state="accepted") for ev in mined.evaluations]
    saved = save_rule_set(accepted, tmp_path / "approved.json")
    checked = evaluate_rules(load_dataset(tmp_path / "rule_miner_changed.csv"), load_rule_set(saved))
    dependency = next(ev for ev in checked.evaluations if ev.rule.kind == "functional_dependency" and ev.rule.columns == ("category", "region"))
    assert 0 in dependency.row_indices and dependency.evidence["unseen_determinants"] >= 1
    assert sum(ev.violations for ev in checked.evaluations) > sum(ev.violations for ev in mined.evaluations)
    for extension in ("xlsx", "parquet"):
        assert load_dataset(tmp_path / f"rule_miner_demo.{extension}").frame.shape == (150, 9)


def test_small_dataset_min_support_one_and_empty_dataset():
    result = RuleMiner().mine(ds({"x": [1]}), options=MiningOptions(min_support=1))
    assert [ev.rule.kind for ev in result.evaluations] == ["not_null"]
    empty = RuleMiner().mine(ds({"x": pl.Series([], dtype=pl.String)}))
    assert empty.evaluations == []


def test_json_safe_report_handles_unusual_parquet_scalars_and_truncation():
    dataset = ds({"x": [float("nan"), float("inf"), 1.]})
    dataset.truncated = True
    result = evaluate_rules(dataset, [RuleSpec("robust_range", ("x",), {"lower": 0, "upper": 2})])
    result.evaluations[0].examples.append({"row": 5, "values": {"date": date(2026, 1, 1), "decimal": Decimal("1.20")}})
    report = result.to_dict()
    encoded = json.dumps(report, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["evaluations"][0]["examples"][0]["values"]["x"] == "nan"
    assert decoded["evaluations"][0]["examples"][-1]["values"]["decimal"] == "1.20"
    assert decoded["evaluations"][0]["rule"]["id"]
    assert any("фрагмент" in note for note in result.notes)
    mined = RuleMiner().mine(dataset)
    assert any("фрагмент" in note for note in mined.notes)


def test_oversized_finite_bounds_rejected_as_value_error():
    with pytest.raises(ValueError):
        RuleSpec("robust_range", ("x",), {"lower": 0, "upper": 10 ** 400})
