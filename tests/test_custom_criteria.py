"""Explicit predicates, safe matching, persistence and constructor workflows."""
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import polars as pl
import pytest
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from data_xray.loaders import Dataset
from data_xray.rules import RuleSpec, EvaluationOptions, evaluate_rules, load_rule_set, save_rule_set
from data_xray.rules.engine import _Context
from data_xray.criteria import evaluate_criteria, evaluations_to_findings
from data_xray.criteria_ui import CriteriaDialog, CriteriaPanel
from data_xray.rule_ui import RulePanel


def dataset(values):
    return Dataset(pl.DataFrame({'x': values}, strict=False), 'memory', 'csv', False, [])


def numeric(lower=0, upper=100, lower_inclusive=False, upper_inclusive=False):
    return RuleSpec('numeric_constraint', ('x',), dict(lower=lower, upper=upper,
        lower_inclusive=lower_inclusive, upper_inclusive=upper_inclusive), 'accepted')


def test_strict_age_and_invalid_filled_values():
    result = evaluate_criteria(dataset(['-1', '0', '0.1', '99.9', '100', 'oops', 'inf', None, '', 'NULL']), [numeric()])
    ev = result.evaluations[0]
    assert (ev.evaluated, ev.passed, ev.violations, ev.skipped) == (7, 2, 5, 3)
    assert ev.row_indices == [0, 1, 4, 5, 6]
    assert ev.evidence['invalid_rows'] == 2
    findings = evaluations_to_findings(result)
    assert len(findings) == 1
    assert findings[0].code == 'custom:' + numeric().id
    assert findings[0].row_indices == [0, 1, 4, 5, 6]
    assert findings[0].severity == 'high' and findings[0].confidence == 'fact'
    assert [example['row'] for example in findings[0].examples] == [1, 2, 5, 6, 7]


@pytest.mark.parametrize('low_inc,high_inc,expected', [(False, False, [0, 2]), (True, False, [2]), (False, True, [0]), (True, True, [])])
def test_all_boundary_combinations(low_inc, high_inc, expected):
    ev = evaluate_rules(dataset(['0', '50', '100']), [numeric(lower_inclusive=low_inc, upper_inclusive=high_inc)]).evaluations[0]
    assert ev.row_indices == expected


def test_open_ended_price_and_zero_width_range():
    assert evaluate_rules(dataset(['0', '-1', '0.01', '001']), [numeric(0, None)]).evaluations[0].row_indices == [0, 1]
    assert evaluate_rules(dataset(['0', '1', '-1']), [numeric(None, 0, upper_inclusive=True)]).evaluations[0].row_indices == [1]
    assert evaluate_rules(dataset(['0', '1']), [numeric(0, 0, True, True)]).evaluations[0].row_indices == [1]


@pytest.mark.parametrize('kwargs', [dict(lower=None, upper=None), dict(lower=3, upper=2), dict(lower=0, upper=0), dict(lower=float('nan')), dict(upper=float('inf')), dict(lower=True), dict(lower_inclusive=1)])
def test_invalid_ranges(kwargs):
    with pytest.raises(ValueError):
        numeric(**kwargs)


@pytest.mark.parametrize('fmt,values,bad', [
    ('%d.%m.%Y', ['29.02.2024', '29.02.2023', '1.02.2024', '01.2.2024', '01.02.2024 ', '31.04.2024'], [1, 2, 3, 4, 5]),
    ('%Y-%m-%d', ['2024-02-29', '2024-13-01', '0000-01-01', '9999-12-31', '２０２４-01-01'], [1, 2, 4]),
    ('%d/%m/%Y', ['01/12/2024', '12/31/2024', '2024/12/01'], [1, 2]),
    ('%Y-%m-%d %H:%M:%S', ['2024-02-29 23:59:59', '2024-02-29 24:00:00', '2024-02-29 1:00:00', '2024-02-29 23:59:60'], [1, 2, 3]),
    ('%Y/%m/%dT%H:%M:%S', ['2024/02/29T23:59:59', '2024/02/29 23:59:59'], [1]),
])
def test_exact_date_formats_and_calendar(fmt, values, bad):
    rule = RuleSpec('date_format', ('x',), {'format': fmt})
    ev = evaluate_rules(dataset(values + [None, '']), [rule]).evaluations[0]
    assert ev.row_indices == bad
    assert ev.skipped == 2
    assert ev.evidence['invalid_rows'] == len(bad)


@pytest.mark.parametrize('fmt', ['%x', '%Y-%m', '%Y-%m-%d %H:%M', '%Y-%m-%d%z', '%Y-%m-%d.*', '%Y-%m-%d %Y', '%Y-%m-%d%', '%Y-%m-%d' * 10])
def test_unsafe_or_ambiguous_date_formats_rejected(fmt):
    with pytest.raises(ValueError):
        RuleSpec('date_format', ('x',), {'format': fmt})


def test_regex_full_string_preserves_zeroes_and_no_trimming():
    rule = RuleSpec('regex', ('x',), {'pattern': '[0-9]{3}'})
    ev = evaluate_rules(dataset(['001', '1', 'X001', '001\n', ' 001', '１２３', None]), [rule]).evaluations[0]
    assert ev.row_indices == [1, 2, 3, 4, 5]
    assert ev.skipped == 1
    # Multiline user flags must not weaken outer whole-string anchors.
    multi = RuleSpec('regex', ('x',), {'pattern': '(?m)[0-9]+'})
    assert evaluate_rules(dataset(['123\n456']), [multi]).evaluations[0].violations == 1


@pytest.mark.parametrize('pattern', [r'(a)\1', '(?=a)a', '(?<=a)b', '[', 'a' * 513, '', 'a{999999999}'])
def test_regex_unsupported_syntax_and_engine_limits(pattern):
    with pytest.raises(ValueError, match='Регулярное выражение'):
        RuleSpec('regex', ('x',), {'pattern': pattern})


def test_regex_nested_quantifiers_use_rust_and_vector_cache(monkeypatch):
    # This pattern is pathological in backtracking engines; Rust has bounded matching.
    rule = RuleSpec('regex', ('x',), {'pattern': '(a+)+'})
    original = _Context.regex
    built = []
    def observed(self, name, pattern):
        if (name, pattern) not in self.matches:
            built.append((name, pattern))
        return original(self, name, pattern)
    monkeypatch.setattr(_Context, 'regex', observed)
    evs = evaluate_rules(dataset(['a' * 100_000 + '!', 'aaaa', None]), [rule, rule]).evaluations
    assert [ev.row_indices for ev in evs] == [[0], [0]]
    assert len(built) == 1


def test_unavailable_and_zero_applies_not_false_success():
    absent = RuleSpec('not_null', ('absent',), review_state='accepted')
    result = evaluate_criteria(dataset([None, '']), [numeric(), absent])
    assert [ev.status for ev in result.evaluations] == ['not_applicable', 'not_applicable']
    assert all(ev.confidence is None for ev in result.evaluations)
    assert 'Отсутствуют' in result.evaluations[1].message
    assert evaluations_to_findings(result) == []
    required = RuleSpec('not_null', ('x',), review_state='accepted')
    assert len(evaluations_to_findings(evaluate_criteria(dataset([None, '']), [required]))) == 1


def test_v2_roundtrip_and_v1_compatibility(tmp_path):
    rules = [numeric(), RuleSpec('date_format', ('x',), {'format': '%d.%m.%Y'}, 'accepted'),
             RuleSpec('regex', ('x',), {'pattern': '[0-9]{3}'}, 'accepted')]
    path = save_rule_set(rules, tmp_path / 'criteria.json')
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 2
    assert {r.id for r in load_rule_set(path)} == {r.id for r in rules}
    old = RuleSpec('not_null', ('x',), review_state='accepted')
    save_rule_set([old], path)
    obj = json.loads(path.read_text(encoding='utf-8'))
    obj['version'] = 1
    path.write_text(json.dumps(obj), encoding='utf-8')
    assert load_rule_set(path) == [old]
    obj['version'] = 3
    path.write_text(json.dumps(obj), encoding='utf-8')
    with pytest.raises(ValueError):
        load_rule_set(path)


@pytest.fixture
def qt_host(tmp_path):
    app = QApplication.instance() or QApplication([])
    host = QMainWindow()
    host.settings = QSettings(str(tmp_path / 'criteria-tests.ini'), QSettings.Format.IniFormat)
    host.dataset = dataset(['0', '50', '100'])
    host._busy = False
    host.rule_source_ready = lambda: host.dataset is not None
    host.start_custom_checks = lambda: None
    host.start_mining = host.open_rule_demo = host.start_rule_check = lambda: None
    yield host
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_dialog_numeric_range_edit_custom_format_and_errors(qt_host):
    dialog = CriteriaDialog(['x'], qt_host)
    assert dialog.preset.findData('age') == dialog.preset.findData('price') == -1
    dialog.lower.setText('0')
    dialog.upper.setText('100')
    assert dialog.get_rule() == numeric()
    dialog.upper.clear()
    assert dialog.get_rule() == numeric(0, None)
    edited = CriteriaDialog(['x'], qt_host, numeric(0, 100, True, False))
    assert edited.rule() == numeric(0, 100, True, False)
    edited.upper.setText('0')
    edited.accept()
    assert edited.result() != QDialog.DialogCode.Accepted
    assert 'Диапазон пуст' in edited.error.text()
    date = RuleSpec('date_format', ('missing',), {'format': '%Y/%m/%dT%H:%M:%S'}, 'accepted')
    custom = CriteriaDialog(['x'], qt_host, date)
    assert custom.get_rule() == date
    dialog.close()
    edited.close()
    custom.close()


def test_dialog_regex_and_exact_categories(qt_host):
    dialog = CriteriaDialog(['x'], qt_host)
    dialog.preset.setCurrentIndex(dialog.preset.findData('regex'))
    dialog.regex_preset.setCurrentIndex(1)
    assert dialog.get_rule().params['pattern'] == '[A-Z]{2}-[0-9]{3}'
    dialog.pattern.setText('(?=a)a')
    dialog.accept()
    assert 'Rust regex' in dialog.error.text()
    dialog.preset.setCurrentIndex(dialog.preset.findData('allowed_values'))
    dialog.values.setText('001;1; x')
    assert dialog.get_rule().params['values'] == [' x', '001', '1']
    dialog.close()


def test_criteria_panel_changed_invalidation_busy_and_deleted_evidence(qt_host):
    panel = CriteriaPanel(qt_host)
    panel.setParent(qt_host)
    panel.rules = [numeric()]
    result = evaluate_criteria(qt_host.dataset, panel.rules)
    panel.apply_result(result)
    assert panel.evaluation(numeric()).violations == 2
    signals = []
    panel.changed.connect(lambda: signals.append(True))
    qt_host._busy = True
    panel.update_buttons()
    assert not panel.add_button.isEnabled() and not panel.delete_button.isEnabled()
    qt_host._busy = False
    panel.invalidate()
    assert panel.rules == [numeric()] and panel.result is None
    panel.delete_rule()
    assert not panel.rules and signals == [True]
    qt_host.dataset = None
    panel.rules = [numeric()]
    panel.refresh()
    assert not panel.check_button.isEnabled() and panel.add_button.isEnabled()
    panel.close()


def test_rule_panel_create_edit_delete_no_stale_resurrection(qt_host, monkeypatch):
    import data_xray.rule_ui as rule_ui
    panel = RulePanel(qt_host)
    panel.setParent(qt_host)
    panel.rules = [numeric()]
    original_result = evaluate_rules(qt_host.dataset, panel.rules)
    panel.apply_result(original_result)
    replacement = numeric(0, None)
    class FakeDialog:
        def __init__(self, *args): pass
        def exec(self): return QDialog.DialogCode.Accepted
        def get_rule(self): return replacement
    monkeypatch.setattr(rule_ui, 'CriteriaDialog', FakeDialog)
    panel.edit_rule()
    assert panel.rules == [replacement]
    assert panel.evaluation(replacement) is None
    panel.apply_result(evaluate_rules(qt_host.dataset, [numeric()]))
    assert panel.rules == [replacement]
    panel.delete_rule()
    panel.apply_result(evaluate_rules(qt_host.dataset, [replacement]))
    assert panel.rules == [] and panel.result.evaluations == []
    panel.create_rule()
    assert panel.rules == [replacement] and panel.evaluation(replacement) is None
    panel.close()


def test_findings_adapter_renders_and_main_window_filters_with_isolated_settings(qt_host, tmp_path):
    from data_xray.models import AnalysisResult, AnalysisOptions
    from data_xray.report import render_report
    from data_xray.ui import MainWindow
    settings = QSettings(str(tmp_path / 'adapter-window.ini'), QSettings.Format.IniFormat)
    settings.setValue('tour_seen', True)
    window = MainWindow(settings=settings)
    window.setParent(qt_host)
    window.dataset = dataset(['0', '50', '100'])
    window.result = AnalysisResult('memory', 'csv', 3, 1, False, [], [], [], AnalysisOptions())
    window.criteria_panel.rules = [numeric()]
    checked = evaluate_criteria(window.dataset, window.criteria_panel.rules)
    window.on_custom_completed(checked)
    assert len(window.visible_findings) == 1
    assert window.visible_findings[0].confidence == 'fact'
    window.search.setText('x')
    assert window.visible_findings
    html = render_report(window.result, criteria_result=checked, criteria_specs=window.criteria_panel.rules)
    assert 'Моя проверка' in html and 'Высокий' in html
    window.criteria_panel.delete_rule()
    assert not window.result.findings
    for dialog in (window.tour, window.help_prompt):
        if dialog:
            dialog.reject()
    window.close()


def test_create_existing_candidate_explicitly_accepts_and_selects(qt_host, monkeypatch):
    from dataclasses import replace
    import data_xray.rule_ui as rule_ui
    panel = RulePanel(qt_host)
    panel.setParent(qt_host)
    candidate = replace(numeric(), review_state="candidate")
    panel.apply_result(evaluate_rules(qt_host.dataset, [candidate]))
    panel.state.setCurrentIndex(panel.state.findData("rejected"))
    panel.search.setText("hidden")
    class ExistingDialog:
        def __init__(self, *args): pass
        def exec(self): return QDialog.DialogCode.Accepted
        def get_rule(self): return numeric()
    monkeypatch.setattr(rule_ui, "CriteriaDialog", ExistingDialog)
    panel.create_rule()
    assert panel.rules == [numeric()]
    assert panel.selected() == numeric()
    assert panel.evaluation(numeric()).rule.review_state == "accepted"
    panel.create_rule()
    assert len(panel.rules) == 1
    panel.close()


def test_edit_rule_keeps_new_selection_under_candidate_filter(qt_host, monkeypatch):
    from dataclasses import replace
    import data_xray.rule_ui as rule_ui
    panel = RulePanel(qt_host)
    panel.setParent(qt_host)
    panel.rules = [replace(numeric(), review_state="candidate"), RuleSpec("not_null", ("x",))]
    panel.refresh()
    panel.state.setCurrentIndex(panel.state.findData("candidate"))
    old = panel.rules[0]
    panel.list.setCurrentRow(panel.visible.index(old))
    replacement = numeric(0, None)
    class ChangedDialog:
        def __init__(self, *args): pass
        def exec(self): return QDialog.DialogCode.Accepted
        def get_rule(self): return replacement
    monkeypatch.setattr(rule_ui, "CriteriaDialog", ChangedDialog)
    panel.edit_rule()
    assert panel.selected() == replacement
    panel.delete_rule()
    assert [r.kind for r in panel.rules] == ["not_null"]
    panel.close()


def test_dialog_numeric_bound_preserves_large_integer(qt_host, tmp_path):
    dialog = CriteriaDialog(['x'], qt_host)
    dialog.lower.setText('9007199254740993')
    dialog.upper.clear()
    rule = dialog.get_rule()
    assert type(rule.params['lower']) is int
    assert rule.params['lower'] == 9007199254740993
    loaded = load_rule_set(save_rule_set([rule], tmp_path / 'large-number.json'))[0]
    assert loaded == rule
    assert evaluate_rules(dataset(['9007199254740993', '9007199254740994']), [loaded]).evaluations[0].row_indices == [0]
    dialog.close()


@pytest.mark.parametrize('text,expected', [('9.007199254740993e15', 9007199254740993),
                                         ('0,25', 0.25), ('', None)])
def test_dialog_numeric_bound_notation(qt_host, text, expected):
    dialog = CriteriaDialog(['x'], qt_host)
    dialog.lower.setText(text)
    dialog.upper.setText('9007199254740994')
    assert dialog.get_rule().params['lower'] == expected
    dialog.close()


@pytest.mark.parametrize('text', ['inf', 'NaN', 'word', '1e999', '9007199254740992.5'])
def test_dialog_numeric_bound_rejects_invalid_or_lossy_input(qt_host, text):
    dialog = CriteriaDialog(['x'], qt_host)
    dialog.lower.setText(text)
    with pytest.raises(ValueError):
        dialog.get_rule()
    dialog.close()
