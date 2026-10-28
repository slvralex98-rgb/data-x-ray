"""Exact uniqueness, bounded evidence, persistence and shared constructor."""
import json
import os
from threading import Event

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import polars as pl
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QFileDialog

from data_xray.checks import AnalysisCancelled
from data_xray.loaders import Dataset
from data_xray.rules import RuleSpec, EvaluationOptions, evaluate_rules, save_rule_set, load_rule_set
from data_xray.rules.engine import _Context, _unique_key
from data_xray.criteria import evaluations_to_findings
from data_xray.criteria_ui import CriteriaDialog, CriteriaPanel, PRESETS, can_edit_rule
from data_xray.rule_ui import RulePanel


def dataset(**columns):
    return Dataset(pl.DataFrame(columns), 'memory', 'csv', False, [])


def unique(*columns):
    return RuleSpec('unique', columns, {}, 'accepted')


def check(data, *columns, options=None, cancel=None):
    return evaluate_rules(data, [unique(*columns)], options, cancel=cancel).evaluations[0]


def test_single_exact_values_all_duplicate_participants_and_findings():
    data = dataset(code=['001', '1', '001', 'A', 'a', ' A', 'A ', 'A', None, '', 'NULL', ' n/a '])
    result = evaluate_rules(data, [unique('code')])
    ev = result.evaluations[0]
    assert (ev.evaluated, ev.passed, ev.violations, ev.skipped) == (8, 4, 4, 4)
    assert ev.row_indices == [0, 2, 3, 7]
    finding, = evaluations_to_findings(result)
    assert finding.count == 4 and finding.total == 8
    assert finding.row_indices == [0, 2, 3, 7]
    assert finding.examples[0]['values'] == {'code': '001'}


def test_multi_tuple_collisions_and_missing_any_operand():
    ev = check(dataset(a=['a|b', 'a', 'a|b', None, '', 'same', 'same'],
                       b=['c', 'b|c', 'c', 'c', 'c', 'NULL', 'NULL']), 'a', 'b')
    assert ev.row_indices == [0, 2]
    assert (ev.evaluated, ev.passed, ev.violations, ev.skipped) == (3, 1, 2, 4)
    assert ev.examples[0]['reason'] == 'duplicate_combination'
    # Missingness in a third field must also exclude the entire row.
    ev = check(dataset(a=['x', 'x', 'x', 'x'], b=['y'] * 4, c=['z', 'z', None, 'NULL']), 'a', 'b', 'c')
    assert ev.row_indices == [0, 1] and ev.skipped == 2


@pytest.mark.parametrize('columns,reason,label', [
    (('x',), 'duplicate_value', 'Значение повторяется'),
    (('x', 'y'), 'duplicate_combination', 'Комбинация значений повторяется'),
])
def test_uniqueness_report_translates_reasons_and_escapes_values(columns, reason, label):
    from data_xray.report import rule_html
    rule = unique(*columns)
    ev = evaluate_rules(dataset(x=['<script>', '<script>'], y=['a', 'a']), [rule]).evaluations[0]
    html = rule_html(rule, ev)
    assert label in html and reason not in html
    assert '&lt;script&gt;' in html and '<script>' not in html


def test_single_multi_no_duplicates_and_unavailable_do_not_false_pass():
    assert check(dataset(x=['001', '1', ' 1', '1 ', 'A', 'a']), 'x').status == 'pass'
    assert check(dataset(x=['a', 'a'], y=['b', 'c']), 'x', 'y').status == 'pass'
    for data in (dataset(x=[None, '', 'NA']), dataset(x=pl.Series([], dtype=pl.String))):
        ev = check(data, 'x')
        assert ev.status == 'not_applicable' and ev.confidence is None
    absent = check(dataset(x=['a']), 'x', 'missing')
    assert absent.status == 'not_applicable' and absent.evidence['missing_columns'] == ['missing']


def test_custom_markers_and_requiredness_are_independent():
    options = EvaluationOptions(null_tokens=('skip',))
    ev = check(dataset(x=['skip', 'skip', 'NULL', 'NULL', None]), 'x', options=options)
    assert ev.row_indices == [2, 3] and ev.skipped == 3
    rules = [unique('x'), RuleSpec('not_null', ('x',))]
    evs = evaluate_rules(dataset(x=[None, None, 'a', 'a']), rules).evaluations
    assert [ev.row_indices for ev in evs] == [[2, 3], [0, 1]]


def test_native_numbers_and_nested_polars_values():
    assert check(dataset(x=[1, 2, 1]), 'x').row_indices == [0, 2]
    assert check(dataset(x=[[1, 2], [1], [1, 2]]), 'x').row_indices == [0, 2]
    assert check(dataset(x=[{'a': 1}, {'a': 2}, {'a': 1}]), 'x').row_indices == [0, 2]
    assert check(dataset(x=[float('nan'), float('nan'), 1.0, 1.0]), 'x').row_indices == [0, 1, 2, 3]


def test_evidence_caps_and_zero_caps():
    data = dataset(x=['duplicate'] * 5000)
    ev = check(data, 'x', options=EvaluationOptions(example_rows=3, max_violation_rows=17))
    assert ev.violations == ev.evaluated == 5000 and ev.passed == 0
    assert ev.row_indices == list(range(17)) and len(ev.examples) == 3
    ev = check(data, 'x', options=EvaluationOptions(example_rows=0, max_violation_rows=0))
    assert ev.violations == 5000 and ev.row_indices == ev.examples == []


@pytest.mark.parametrize('dtype', [pl.Datetime('ns'), pl.Duration('ns'), pl.Time])
def test_temporal_nanosecond_precision_is_not_lost(dtype):
    values = pl.Series('x', [1, 2, 1], dtype=pl.Int64).cast(dtype)
    assert check(dataset(x=values), 'x').row_indices == [0, 2]
    nested = pl.DataFrame({'x': values}).select(pl.struct('x').alias('nested'))
    assert check(Dataset(nested, 'memory', 'parquet', False, []), 'nested').row_indices == [0, 2]
    lists = pl.Series('x', [[1], [2], [1]]).cast(pl.List(dtype))
    assert check(dataset(x=lists), 'x').row_indices == [0, 2]


def test_cancel_during_counting_and_evidence(monkeypatch):
    import data_xray.rules.engine as engine
    for trigger in (600, 3000):
        event = Event()
        calls = 0
        def observed(columns, row):
            nonlocal calls
            calls += 1
            if calls == trigger:
                event.set()
            return _unique_key(columns, row)
        monkeypatch.setattr(engine, '_unique_key', observed)
        with pytest.raises(AnalysisCancelled):
            check(dataset(x=['a'] * 2500), 'x', cancel=event)
        assert calls <= trigger + 512
    event = Event()
    event.set()
    with pytest.raises(AnalysisCancelled):
        check(dataset(x=['a']), 'x', cancel=event)


@pytest.mark.parametrize('columns,params', [((), {}), (('x', 'x'), {}), (tuple(str(i) for i in range(33)), {}),
    (('x',), {'ignored': 1}), (('x',), []), (('',), {}), (('x', 2), {})])
def test_strict_unique_schema(columns, params):
    with pytest.raises(ValueError):
        RuleSpec('unique', columns, params)


def test_unique_canonical_id_json_and_duplicate_rejection(tmp_path):
    left, right = unique('b', 'a'), unique('a', 'b')
    assert left == right and left.id == right.id and left.columns == ('a', 'b')
    assert 'комбинация' in left.description and '«a»' in left.description and '«b»' in left.description
    path = save_rule_set([left, unique('a')], tmp_path / 'unique.json')
    obj = json.loads(path.read_text(encoding='utf-8'))
    assert obj['version'] == 2
    assert {r.id for r in load_rule_set(path)} == {left.id, unique('a').id}
    with pytest.raises(ValueError, match='повторяющееся'):
        save_rule_set([left, right], path)
    obj['rules'][0]['params'] = {'code': 'unsafe'}
    path.write_text(json.dumps(obj), encoding='utf-8')
    with pytest.raises(ValueError):
        load_rule_set(path)


@pytest.mark.parametrize('version', [1, 2])
def test_legacy_numeric_and_uuid_json_are_still_checked(version, tmp_path):
    # Historical age/price presets stored ordinary numeric constraints.
    rules = [RuleSpec('numeric_constraint', ('x',), {'lower': 0, 'upper': bound,
             'lower_inclusive': False, 'upper_inclusive': False}, 'accepted') for bound in (100, None)]
    rules.append(RuleSpec('string_shape', ('id',), {'format': 'uuid'}, 'accepted'))
    path = save_rule_set(rules, tmp_path / 'legacy.json')
    obj = json.loads(path.read_text(encoding='utf-8'))
    obj['version'] = version
    path.write_text(json.dumps(obj), encoding='utf-8')
    loaded = load_rule_set(path)
    assert {r.id for r in loaded} == {r.id for r in rules}
    result = evaluate_rules(dataset(x=['0', '100', '1'], id=['bad', '12345678-1234-1234-1234-123456789abc', None]), loaded)
    assert {ev.rule.id: ev.row_indices for ev in result.evaluations} == {
        rules[0].id: [0, 1], rules[1].id: [0], rules[2].id: [0]}


@pytest.fixture
def host(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.settings = QSettings(str(tmp_path / 'unique-tests.ini'), QSettings.Format.IniFormat)
    window.dataset = dataset(a=['x', 'x'], b=['y', 'y'])
    window._busy = False
    window.rule_source_ready = lambda: window.dataset is not None
    window.start_custom_checks = window.start_mining = window.open_rule_demo = window.start_rule_check = lambda: None
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def select(dialog, kind):
    dialog.preset.setCurrentIndex(dialog.preset.findData(kind))


def test_constructor_single_multi_validation_selection_and_legacy_edit(host):
    assert not {'age', 'price', 'uuid'} & {kind for _, kind in PRESETS}
    dialog = CriteriaDialog(['a', 'b'], host)
    select(dialog, 'unique')
    assert dialog.get_rule() == unique('a')
    select(dialog, 'unique_multi')
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert 'двух' in dialog.error.text()
    for i in range(2):
        dialog.unique_columns.item(i).setCheckState(Qt.CheckState.Checked)
    assert dialog.get_rule() == unique('a', 'b')
    assert '«a»' in dialog.unique_selection.text() and '«b»' in dialog.unique_selection.text()
    assert can_edit_rule(dialog.get_rule())
    edited = CriteriaDialog(['a'], host, dialog.get_rule())
    assert edited.get_rule() == unique('a', 'b')
    old = RuleSpec('string_shape', ('a',), {'format': 'uuid'}, 'accepted')
    legacy = CriteriaDialog(['a'], host, old)
    assert legacy.get_rule() == old and 'сохраненное' in legacy.preset.currentText()


def test_constructor_before_loading_exact_names_and_multi_limit(host):
    dialog = CriteriaDialog([], host)
    select(dialog, 'unique_multi')
    for name in ('id;one', ' x ', 'id;one'):
        dialog.unique_name.setText(name)
        dialog._add_typed_unique_column()
    assert dialog.get_rule() == unique('id;one', ' x ')
    assert dialog.unique_columns.count() == 2
    for i in range(31):
        dialog._add_unique_column(str(i), checked=True)
    with pytest.raises(ValueError, match='число столбцов'):
        dialog.get_rule()


@pytest.mark.parametrize('panel_type,module', [(CriteriaPanel, 'data_xray.criteria_ui'), (RulePanel, 'data_xray.rule_ui')])
def test_both_panels_create_edit_save_open_delete_unique(host, monkeypatch, tmp_path, panel_type, module):
    import importlib
    panel = panel_type(host)
    panel.setParent(host)
    replacement = unique('a', 'b')
    class AcceptedDialog:
        def __init__(self, *args): pass
        def exec(self): return QDialog.DialogCode.Accepted
        def get_rule(self): return replacement
    monkeypatch.setattr(importlib.import_module(module), 'CriteriaDialog', AcceptedDialog)
    (panel.add_rule if panel_type is CriteriaPanel else panel.create_rule)()
    assert panel.selected() == replacement
    assert (panel.edit_button if panel_type is CriteriaPanel else panel.edit_action).isEnabled()
    panel.apply_result(evaluate_rules(host.dataset, panel.rules))
    assert panel.evaluation(replacement).row_indices == [0, 1]
    assert '«a»' in panel.detail.toPlainText() and '«b»' in panel.detail.toPlainText()
    path = str(tmp_path / 'panel.json')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (path, ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (path, ''))
    panel.save_set()
    panel.delete_rule()
    assert panel.rules == []
    panel.open_set()
    assert panel.rules == [replacement] and panel.result is None
    replacement = unique('b')
    panel.edit_rule()
    assert panel.rules == [replacement]
    panel.delete_rule()
    assert panel.rules == []
