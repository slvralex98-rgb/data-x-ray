import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QSettings, QElapsedTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from data_xray.ui import MainWindow
from data_xray.rules import RuleMiner, RuleSpec, evaluate_rules, save_rule_set
from data_xray.rules.demo import create_rule_demo
from data_xray.loaders import load_dataset
from data_xray.engine import Analyzer
from data_xray.report import render_report, rule_html


def settle(window, predicate=lambda: True):
    timer = QElapsedTimer()
    timer.start()
    while (window._busy or not predicate()) and timer.elapsed() < 15000:
        QTest.qWait(5)
    assert not window._busy and predicate()
    QApplication.instance().processEvents()


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "isolated.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings=settings)
    win.show()
    yield win
    if win.worker and win.worker.isRunning():
        win.worker.cancel_event.set()
        win.worker.wait(10000)
        app.processEvents()
    win.close()


def mine(window, tmp_path):
    path = create_rule_demo(tmp_path)
    window.select_path(path, auto_start=True)
    settle(window)
    window.start_mining()
    assert not window.open_button.isEnabled()
    assert not window.rule_panel.check_button.isEnabled()
    settle(window)
    return path


def test_review_save_reload_next_file_violations(window, tmp_path, monkeypatch):
    path = mine(window, tmp_path)
    panel = window.rule_panel
    assert window.tabs.count() == 7 and window.tabs.tabText(2) == "Данные"
    assert len(panel.rules) == 35
    panel.search.setText("contact")
    target = next(r for r in panel.visible if r.kind == "string_shape" and r.params == {"format": "email"})
    panel.list.setCurrentRow(panel.visible.index(target))
    panel.accept_button.click()
    assert panel.selected().review_state == "accepted"
    assert panel.evaluation(panel.selected()).row_indices == [22]
    panel.violations_button.click()
    assert window.tabs.currentIndex() == 2
    assert window.preview_view.model().rows[0][0] == 23
    saved = tmp_path / "accepted.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(saved), ""))
    panel.save_set()
    assert len(json.loads(saved.read_text(encoding="utf-8"))["rules"]) == 1
    window.select_path(tmp_path / "rule_miner_changed.csv")
    assert panel.result is None and len(panel.rules) == 1
    assert not panel.mine_button.isEnabled() and not panel.violations_button.isEnabled()
    assert "Не проверено" in panel.detail.toPlainText()
    window.start_analysis()
    settle(window)
    window.start_rule_check()
    settle(window)
    assert panel.result.evaluations[0].row_indices == [0, 22]
    panel.violations_button.click()
    assert [r[0] for r in window.preview_view.model().rows] == [1, 23]
    # Importing another set must remove all previous evidence.
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(saved), ""))
    panel.open_set()
    assert panel.result is None
    assert '<h3 id="rules">Поиск правил' not in render_report(window.result, panel.result)
    untested = render_report(window.result, panel.result, rule_specs=panel.rules)
    assert 'id="rules"' in untested and "Не проверено" in untested
    assert "Нарушения: 2" not in untested


def test_set_errors_and_cancel_keep_state(window, tmp_path, monkeypatch):
    mine(window, tmp_path)
    panel = window.rule_panel
    old = panel.result
    old_rules = panel.rules
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema":"wrong"}', encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    panel.open_set()
    assert panel.result is old and panel.rules is old_rules
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    panel.open_set()
    assert panel.result is old
    window.start_mining()
    window.cancel_analysis()
    settle(window)
    assert panel.result is old
    monkeypatch.setattr("data_xray.rule_ui.RuleMiner.mine", lambda *a, **k: (_ for _ in ()).throw(ValueError("broken")))
    window.start_mining()
    settle(window)
    assert panel.result is old


def test_source_settings_reanalysis_and_export_filters(window, tmp_path, monkeypatch):
    mine(window, tmp_path)
    panel = window.rule_panel
    all_count = len(panel.rules)
    panel.search.setText("contact")
    html = tmp_path / "full.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(html), ""))
    window.export()
    assert html.read_text(encoding="utf-8").count('Кандидат · Условие</div>') == all_count
    window.row_limit.setValue(100)
    assert not panel.mine_button.isEnabled()
    window.start_mining()
    assert not window._busy
    window.row_limit.setValue(200000)
    window.start_analysis()
    settle(window)
    assert panel.result is None and not panel.rules


def test_rule_demo_chains_workers_and_preserves_general_demo(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QStandardPaths
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda *a: str(tmp_path))
    window.open_rule_demo()
    settle(window, lambda: window.rule_panel.result is not None)
    assert window.dataset.frame.shape == (150, 9)
    assert window.tabs.currentIndex() == 4
    window.open_demo()
    settle(window)
    assert len(window.result.findings) == 17
    assert window.rule_panel.result is None


def test_unavailable_and_escaped_html(tmp_path):
    dataset = load_dataset(create_rule_demo(tmp_path))
    rules = [RuleSpec("not_null", ("<script>alert(1)</script>",), review_state="accepted")]
    checked = evaluate_rules(dataset, rules)
    text = rule_html(rules[0], checked.evaluations[0])
    assert "Не применимо" in text and "Доля выполнения в применимых записях: <strong>Не применимо" in text
    assert "<script>" not in text and "&lt;script&gt;" in text
    base = Analyzer().analyze(dataset)
    assert 'id="rules"' in render_report(base, checked)
    checked.source = "another.csv"
    assert 'id="rules"' not in render_report(base, checked)


def test_mining_preserves_decisions_and_check_is_accepted_only(window, tmp_path):
    mine(window, tmp_path)
    panel = window.rule_panel
    assert not panel.check_button.isEnabled()
    panel.list.setCurrentRow(0)
    accepted = panel.selected().id
    panel.review("accepted")
    panel.list.setCurrentRow(1)
    rejected = panel.selected().id
    panel.review("rejected")
    window.start_mining()
    settle(window)
    states = {r.id: r.review_state for r in panel.rules}
    assert states[accepted] == "accepted" and states[rejected] == "rejected"
    window.start_rule_check()
    settle(window)
    assert [r.id for r in panel.result.rules] == [accepted]
    assert len(panel.rules) == 35
    assert sum(panel.evaluation(r) is None for r in panel.rules) == 34
    window.select_path(tmp_path / "rule_miner_changed.csv", auto_start=True)
    settle(window)
    window.start_mining()
    settle(window)
    assert next(r for r in panel.rules if r.id == accepted).review_state == "accepted"


def test_old_worker_signals_cannot_settle_new_run(window, tmp_path, monkeypatch):
    from threading import Event
    mine(window, tmp_path)
    old = window.worker
    original = RuleMiner.mine
    gate = Event()
    def delayed(*args, **kwargs):
        gate.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(RuleMiner, "mine", delayed)
    window.start_mining()
    old.finished.emit()
    old.failed.emit("stale error must be ignored")
    old.progress.emit(99, "stale progress")
    QApplication.instance().processEvents()
    assert window._busy and window.loading_overlay.state == "loading"
    assert not window.open_button.isEnabled()
    assert window.loading_overlay.stage.text() != "stale progress · 99%"
    assert not window.close()
    assert window.worker.cancel_event.is_set()
    gate.set()
    settle(window)


def test_cli_nonfinite_parquet_is_strict_json(tmp_path):
    import polars as pl
    path = tmp_path / "nonfinite.parquet"
    pl.DataFrame({"value": [1.0] * 25 + [float("nan"), float("inf"), -float("inf")]}).write_parquet(path)
    html, report = tmp_path / "r.html", tmp_path / "r.json"
    completed = subprocess.run([sys.executable, "-m", "data_xray", str(path), "--mine-rules", "--html", str(html), "--json", str(report)], capture_output=True)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    assert payload["rule_miner"]


def test_cli_rule_modes_and_default_compatibility(tmp_path):
    path = create_rule_demo(tmp_path)
    def run(*args):
        return subprocess.run([sys.executable, "-m", "data_xray", str(path), *map(str, args)], capture_output=True)
    html, report = tmp_path / "r.html", tmp_path / "r.json"
    assert run("--mine-rules").returncode == 2
    assert run("--mine-rules", "--rule-set", "x", "--html", html).returncode == 2
    assert run("--mine-rules", "--html", html, "--json", report).returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert all(ev["rule"]["review_state"] == "candidate" for ev in payload["rule_miner"]["evaluations"])
    dataset = load_dataset(path)
    rule = next(r for r in RuleMiner().mine(dataset).rules if r.kind == "string_shape" and r.columns == ("contact",))
    saved = tmp_path / "rules.json"
    save_rule_set([replace(rule, review_state="accepted")], saved)
    completed = subprocess.run([sys.executable, "-m", "data_xray", str(tmp_path / "rule_miner_changed.csv"), "--rule-set", str(saved), "--html", str(html), "--json", str(report)], capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["rule_miner"]["evaluations"][0]["row_indices"] == [0, 22]
    assert run("--html", html, "--json", report).returncode == 0
    assert "rule_miner" not in json.loads(report.read_text(encoding="utf-8"))
    assert 'id="rules"' not in html.read_text(encoding="utf-8")
    assert run("--rule-set", tmp_path / "missing.json", "--html", html).returncode == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
def test_actual_batch_rule_modes_return_parser_error():
    root = Path(__file__).resolve().parents[1]
    for arguments in (["--mine-rules"], ["--rule-set", "missing.json"]):
        command = subprocess.list2cmdline([str(root / "run.cmd"), *arguments])
        completed = subprocess.run(f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{command}"', cwd=root,
                                   capture_output=True, timeout=10)
        assert completed.returncode == 2, completed.stderr
        assert b"--html" in completed.stderr


@pytest.mark.parametrize("control", ["row_limit", "separator", "encoding", "sheet"])
def test_changed_source_settings_clear_evidence_and_export(window, tmp_path, monkeypatch, control):
    mine(window, tmp_path)
    panel = window.rule_panel
    panel.accept_button.click()
    selected_id = panel.selected().id
    widget = getattr(window, control)
    if control == "row_limit":
        widget.setValue(100)
    elif control == "sheet":
        widget.addItem("Another sheet", "Another sheet")
        widget.setCurrentIndex(1)
    else:
        widget.setCurrentIndex(1)
    assert panel.result is None
    assert [r.id for r in panel.rules] == [selected_id]
    assert "Не проверено" in panel.detail.toPlainText()
    assert not panel.mine_button.isEnabled() and not panel.violations_button.isEnabled()
    output = tmp_path / "old-xray.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    window.export()
    assert 'id="rules"' not in output.read_text(encoding="utf-8")


def test_selected_unanalyzed_file_cannot_export_previous_rules(window, tmp_path, monkeypatch):
    mine(window, tmp_path)
    panel = window.rule_panel
    panel.accept_button.click()
    window.select_path(tmp_path / "not-yet-read.csv")
    output = tmp_path / "old-xray.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    window.export()
    assert 'id="rules"' not in output.read_text(encoding="utf-8")
    assert panel.result is None and not panel.violations_button.isEnabled()


def test_filters_review_buttons_and_save_failure(window, tmp_path, monkeypatch):
    mine(window, tmp_path)
    panel = window.rule_panel
    panel.search.setText("contact")
    selected = next(r for r in panel.visible if r.kind == "string_shape")
    panel.list.setCurrentRow(panel.visible.index(selected))
    panel.reject_button.click()
    assert panel.selected().review_state == "rejected"
    panel.state.setCurrentIndex(panel.state.findData("rejected"))
    assert [r.id for r in panel.visible] == [selected.id]
    panel.candidate_button.click()
    assert panel.selected() is None and not panel.accept_button.isEnabled()
    panel.state.setCurrentIndex(panel.state.findData("candidate"))
    panel.list.setCurrentRow(next(i for i, r in enumerate(panel.visible) if r.id == selected.id))
    panel.accept_button.click()
    panel.state.setCurrentIndex(panel.state.findData("violations"))
    assert all(panel.evaluation(r).violations > 0 for r in panel.visible)
    panel.search.setText("nothing-matches")
    assert not panel.visible and not panel.violations_button.isEnabled()
    panel.search.clear()
    panel.state.setCurrentIndex(panel.state.findData("accepted"))
    assert [r.id for r in panel.visible] == [selected.id]
    old_rules, old_result = panel.rules, panel.result
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    panel.save_button.click()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[1:]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "missing" / "set.json"), ""))
    panel.save_button.click()
    assert warnings[0][0] == "Набор не сохранен"
    assert panel.rules is old_rules and panel.result is old_result


def test_untested_and_mismatched_report_never_attaches_evidence(tmp_path):
    dataset = load_dataset(create_rule_demo(tmp_path))
    analysis = Analyzer().analyze(dataset)
    rule = RuleSpec("allowed_values", ("category",), {"values": ["<script>bad&value</script>"]}, "accepted")
    checked = evaluate_rules(dataset, [rule])
    checked.source = "different.csv"
    html = render_report(analysis, checked, rule_specs=[rule])
    assert "Не проверено" in html and "<script>" not in html
    assert "&lt;script&gt;bad&amp;value&lt;/script&gt;" in html
    assert "Нарушения: 150" not in html


def test_unavailable_rules_are_explicit_in_ui(window, tmp_path):
    mine(window, tmp_path)
    panel = window.rule_panel
    panel.rules = [RuleSpec("not_null", ("missing-column",), review_state="accepted"),
                   RuleSpec("conditional_required", ("status", "closed_at"), {"value": "never-seen"}, "accepted")]
    panel.result = None
    panel.refresh()
    panel.check_button.click()
    settle(window)
    assert all(ev.status == "not_applicable" and ev.confidence is None for ev in panel.result.evaluations)
    for row in range(len(panel.visible)):
        panel.list.setCurrentRow(row)
        assert "Не применимо" in panel.detail.toPlainText()
        assert not panel.violations_button.isEnabled()


def test_finished_rule_worker_releases_previous_dataset(window, tmp_path):
    import gc
    import weakref
    mine(window, tmp_path)
    previous_worker = window.worker
    previous_dataset = weakref.ref(window.dataset)
    assert previous_worker.dataset is None and previous_worker.rules is None
    window.select_path(tmp_path / "rule_miner_changed.csv", auto_start=True)
    settle(window)
    gc.collect()
    assert previous_dataset() is None
