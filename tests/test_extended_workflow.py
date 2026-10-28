"""Cross-module acceptance: custom conditions, review, passport and real CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QSettings, QElapsedTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from data_xray.ui import MainWindow
from data_xray.rules import RuleSpec, save_rule_set
from data_xray.report import render_report
from data_xray.passport import save_passport
from data_xray.finding_review import FindingReview, save_review_session, load_review_session


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(settings=QSettings(str(tmp_path / "isolated.ini"), QSettings.Format.IniFormat))
    win.show()
    yield win
    if win.worker and win.worker.isRunning():
        win.worker.cancel_event.set()
        win.worker.wait(10000)
        app.processEvents()
    win.close()


def wait(win, predicate=lambda: True):
    timer = QElapsedTimer()
    timer.start()
    while (win._busy or not predicate()) and timer.elapsed() < 15000:
        QTest.qWait(10)
    assert not win._busy and predicate()
    QApplication.processEvents()


def source(tmp_path):
    path = tmp_path / "ages.csv"
    path.write_text("age,price,date\n0,0,2024-02-29\n1,3,2024-02-30\n99,2,29.02.2024\n100,-1,2024-01-01\n,1,\nwrong,5,2024-12-01\n", encoding="utf-8")
    return path


def age_rule():
    return RuleSpec("numeric_constraint", ("age",), {
        "lower": 0, "upper": 100, "lower_inclusive": False, "upper_inclusive": False}, "accepted")


def test_automatic_criteria_review_and_invalidation(window, tmp_path):
    panel = window.criteria_panel
    panel.rules = [age_rule()]
    panel._changed()
    window.select_path(source(tmp_path), auto_start=True)
    wait(window, lambda: panel.result is not None)
    evaluation = panel.result.evaluations[0]
    assert (evaluation.evaluated, evaluation.violations, evaluation.skipped) == (5, 3, 1)
    assert evaluation.row_indices == [0, 3, 5]
    finding = next(f for f in window.result.findings if f.code.startswith("custom:"))
    assert finding.row_indices == [0, 3, 5]
    saved_review = tmp_path / "custom-review.json"
    review_snapshot = FindingReview(window.result)
    review_snapshot.mark(finding, "confirmed", "Проверка моего критерия")
    save_review_session(window.result, review_snapshot, saved_review)
    reopened_result, reopened_review = load_review_session(saved_review)
    assert reopened_review.matches(window.result)
    reopened_finding = next(f for f in reopened_result.findings if f.code == finding.code)
    assert reopened_review.get_state(reopened_finding)["comment"] == "Проверка моего критерия"
    assert reopened_finding.row_indices == [0, 3, 5]
    assert "Моя проверка" in render_report(window.result, criteria_result=panel.result)
    builtin = next(f for f in window.result.findings if not f.code.startswith("custom:"))
    review = window.finding_review
    review.review.mark(builtin, "confirmed", "Проверено вручную")
    window.start_custom_checks()
    wait(window)
    assert review.review.get_state(builtin)["comment"] == "Проверено вручную"
    panel.rules = []
    panel._changed()
    assert not any(f.code.startswith("custom:") for f in window.result.findings)
    assert review.review.get_state(builtin)["state"] == "confirmed"
    assert window.passport_panel.current is not None
    window.row_limit.setValue(2)
    assert window.passport_panel.current is None and panel.result is None


def test_review_toolbar_and_report(window, tmp_path, monkeypatch):
    window.select_path(source(tmp_path), auto_start=True)
    wait(window)
    bar = window.finding_review
    finding = window.visible_findings[0]
    bar.state.setCurrentIndex(bar.state.findData("not_problem"))
    bar.apply_button.click()
    assert bar.review.get_state(finding)["state"] == "not_problem"
    bar.remove_button.click()
    assert finding not in window.visible_findings
    bar.state_filter.setCurrentIndex(bar.state_filter.findData("deleted"))
    assert window.visible_findings == [finding]
    assert bar.remove_button.text() == "Восстановить"
    html = render_report(window.result, review=bar.review)
    assert "Удалено" in html and "Не проблема" in html
    saved = tmp_path / "review.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(saved), ""))
    bar.save_button.click()
    assert saved.exists()
    bar.remove_button.click()
    assert not bar.review.get_state(finding)["deleted"]


def test_passport_baseline_survives_new_source_and_report(window, tmp_path):
    path = source(tmp_path)
    window.select_path(path, auto_start=True)
    wait(window)
    panel = window.passport_panel
    panel.set_baseline(panel.current)
    panel.compare_button.click()
    assert panel.comparison["rows"]["delta"] == 0
    old = panel.baseline
    other = tmp_path / "next.csv"
    other.write_text("age,extra\n3,x\n", encoding="utf-8")
    window.select_path(other)
    assert panel.current is None and panel.baseline == old
    window.start_analysis()
    wait(window)
    panel.compare_button.click()
    assert panel.comparison["added"] == ["extra"]
    assert sorted(panel.comparison["removed"]) == ["date", "price"]
    assert panel.comparison["rows"]["delta"] == -5
    html = render_report(window.result, passport=panel.export_content())
    assert "Паспорт таблицы" in html and "extra" in html
    assert html.count('id="passport"') == 1
    window.section_picker.setCurrentIndex(6)
    assert window.tabs.currentIndex() == 6


def test_real_cli_custom_and_passport(tmp_path):
    path = source(tmp_path)
    criteria = tmp_path / "criteria.json"
    save_rule_set([age_rule()], criteria)
    baseline = tmp_path / "passport.json"
    html = tmp_path / "report.html"
    report = tmp_path / "report.json"
    common = [sys.executable, "-m", "data_xray", str(path), "--criteria", str(criteria),
              "--html", str(html), "--json", str(report)]
    result = subprocess.run(common + ["--save-passport", str(baseline)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["custom_criteria"]["evaluations"][0]["violations"] == 3
    assert payload["table_passport"]["current"]["rows"] == 6
    assert baseline.exists()
    result = subprocess.run(common + ["--compare-passport", str(baseline)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["table_passport"]["comparison"]["rows"]["delta"] == 0
    assert "Мои проверки" in html.read_text(encoding="utf-8")
    invalid = subprocess.run([sys.executable, "-m", "data_xray", str(path), "--criteria", str(criteria)], capture_output=True, timeout=15)
    assert invalid.returncode == 2


@pytest.mark.parametrize("outcome", ["cancel", "fail"])
def test_custom_worker_cancel_error_preserves_committed_evidence(window, tmp_path, monkeypatch, outcome):
    from threading import Event
    from data_xray.checks import AnalysisCancelled
    import data_xray.rule_ui as rule_ui
    window.criteria_panel.rules = [age_rule()]
    window.select_path(source(tmp_path), auto_start=True)
    wait(window, lambda: window.criteria_panel.result is not None)
    previous, evaluations, passport = window.result, window.criteria_panel.result, window.passport_panel.current
    started = Event()
    def controlled(dataset, rules, options, progress, cancel):
        started.set()
        if outcome == "cancel":
            cancel.wait(5)
            raise AnalysisCancelled()
        raise ValueError("Controlled criteria failure")
    monkeypatch.setattr(rule_ui, "evaluate_rules", controlled)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    window.criteria_panel.check_button.click()
    assert window._busy
    for button in (window.criteria_panel.add_button, window.criteria_panel.check_button,
                   window.passport_panel.save_button, window.finding_review.open_button):
        assert not button.isEnabled()
    if outcome == "cancel":
        window.cancel_button.click()
    wait(window)
    assert started.is_set()
    assert window.result is previous and window.criteria_panel.result is evaluations
    assert window.passport_panel.current is passport
    assert bool(warnings) == (outcome == "fail")
