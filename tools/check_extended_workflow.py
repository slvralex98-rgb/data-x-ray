"""Repeatable native Windows acceptance; temporary INI settings and test outputs."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QElapsedTimer, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QMessageBox
from data_xray.criteria_ui import CriteriaDialog, PRESETS
from data_xray.ui import MainWindow
from data_xray.rules import load_rule_set
from data_xray.demo import create_demo


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "docs"
    app = QApplication([])
    def unexpected_warning(*args, **kwargs):
        raise AssertionError("Unexpected warning: " + " | ".join(str(v) for v in args[1:]))
    warning_guard = patch.object(QMessageBox, "warning", side_effect=unexpected_warning)
    warning_guard.start()
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
    with TemporaryDirectory(prefix="extended-workflow-") as temporary:
        temp = Path(temporary)
        window = MainWindow(settings=QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat))
        window.show()
        app.processEvents()

        def settle(predicate=lambda: True):
            timer = QElapsedTimer()
            timer.start()
            while (window._busy or window.loading_overlay.isVisible() or not predicate()) and timer.elapsed() < 30000:
                QTest.qWait(10)
            assert not window._busy and predicate(), "Background work did not settle"
            app.processEvents()

        def capture(widget, name):
            QTest.qWait(100)
            assert widget.grab().save(str(output / ("extended-" + name + ".png")))

        def save_button(button, path):
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                button.click()
            assert path.exists(), path

        def open_button(button, path):
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
                button.click()

        def edit_dialog(button, configure):
            completed = []
            def drive():
                dialog = QApplication.activeModalWidget()
                assert isinstance(dialog, CriteriaDialog)
                configure(dialog)
                dialog.accept()
                assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
                completed.append(True)
            QTimer.singleShot(100, drive)
            button()
            assert completed

        try:
            # Open the supplied six checks using the actual panel button.
            checks = window.criteria_panel
            open_button(checks.open_button, root / "examples/custom_criteria.json")
            assert len(checks.rules) == 6
            window.select_path(root / "examples/custom_checks_demo.csv", auto_start=True)
            settle(lambda: checks.result is not None)
            expected = {("numeric_constraint", "age"): [2, 3, 6], ("numeric_constraint", "price"): [2, 3],
                        ("date_format", "event_date"): [2, 3], ("regex", "code"): [3, 6],
                        ("not_null", "age"): [5], ("not_null", "event_date"): [5]}
            actual = {(ev.rule.kind, ev.rule.columns[0]): [r + 1 for r in ev.row_indices] for ev in checks.result.evaluations}
            assert actual == expected, actual
            checks.check_button.click()
            settle()
            for finding in [f for f in window.result.findings if f.code.startswith("custom:")]:
                window.tabs.setCurrentIndex(0)
                window.findings_list.setCurrentRow(window.visible_findings.index(finding))
                window.affected_button.click()
                assert [r[0] for r in window.preview_view.model().rows] == [r + 1 for r in finding.row_indices]

            # Review, deleted items, isolated saved snapshots and combined HTML.
            window.tabs.setCurrentIndex(0)
            review = window.finding_review
            finding = window.visible_findings[0]
            window.findings_list.setCurrentRow(0)
            for status in ("confirmed", "not_problem", "fixed"):
                review.state.setCurrentIndex(review.state.findData(status))
                review.apply_button.click()
                assert review.review.get_state(finding)["state"] == status
            with patch.object(QInputDialog, "getMultiLineText", return_value=("Проверено <вручную>", True)):
                review.comment_button.click()
            review.remove_button.click()
            review.state_filter.setCurrentIndex(review.state_filter.findData("deleted"))
            assert window.visible_findings == [finding]
            save_button(review.save_button, temp / "review.json")
            current_result, current_dataset = window.result, window.dataset
            open_button(review.open_button, temp / "review.json")
            assert window.result is current_result and window.dataset is current_dataset
            assert review.snapshot_dialog.isVisible()
            capture(review.snapshot_dialog, "saved-review")
            review.snapshot_dialog.close()

            passport = window.passport_panel
            save_button(passport.save_button, temp / "passport.json")
            open_button(passport.open_button, temp / "passport.json")
            passport.compare_button.click()
            assert passport.comparison["rows"]["delta"] == 0
            window.rule_panel.rules = [checks.rules[0]]
            window.rule_panel.refresh()
            window.start_rule_check()
            settle()
            save_button(window.export_button, temp / "combined.html")
            markup = (temp / "combined.html").read_text(encoding="utf-8")
            for text in ("Паспорт таблицы", "Поиск правил", "Мои проверки", "Исправлено", "Удалено", "Проверено &lt;вручную&gt;"):
                assert text in markup, text
            shutil.copyfile(temp / "combined.html", output / "passport-criteria-demo-report.html")
            review.remove_button.click()
            review.state_filter.setCurrentIndex(review.state_filter.findData("active"))
            assert not review.review.get_state(finding)["deleted"]

            # All seven panels at both supported window sizes; keep exact geometry.
            for width, height, suffix in ((1400, 900, "wide"), (1000, 680, "compact")):
                window.resize(width, height)
                for index, name in enumerate(("problems", "profile", "data", "methods", "rules", "passport", "criteria")):
                    window.section_picker.setCurrentIndex(index)
                    app.processEvents()
                    assert window.tabs.currentIndex() == index
                    assert (window.width(), window.height()) == (width, height), (name, window.size())
                    capture(window, name + "-" + suffix)

            # Every constructor preset is usable; validation remains inside dialog.
            dialog = CriteriaDialog(window.dataset.frame.columns, window)
            dialog.show()
            for title, key in PRESETS:
                dialog.preset.setCurrentIndex(dialog.preset.findData(key))
                if key == "numeric_constraint": dialog.lower.setText("0")
                if key == "unique_multi":
                    for index in range(2):
                        dialog.unique_columns.item(index).setCheckState(Qt.CheckState.Checked)
                if key == "allowed_values": dialog.values.setText("a;b")
                if key == "regex": dialog.regex_preset.setCurrentIndex(1)
                dialog.get_rule()
                app.processEvents()
                assert dialog.width() <= 1000 and dialog.height() <= 680, (title, dialog.size())
            for index in range(1, dialog.regex_preset.count()):
                dialog.regex_preset.setCurrentIndex(index)
                dialog.get_rule()
            dialog.regex_preset.setCurrentIndex(1)
            capture(dialog, "constructor-regex")
            dialog.pattern.setText("(?=a)a")
            dialog.accept()
            assert dialog.isVisible() and "Rust regex" in dialog.error.text()
            capture(dialog, "constructor-error")
            dialog.reject()

            # Add/edit/delete through the actual modal constructor.
            edit_dialog(checks.add_button.click, lambda d: (d.column.setCurrentText("person_id"), d.preset.setCurrentIndex(d.preset.findData("numeric_constraint")), d.lower.setText("0")))
            assert len(checks.rules) == 7 and checks.result is None
            checks.list.setCurrentRow(6)
            old_id = checks.rules[6].id
            edit_dialog(checks.edit_button.click, lambda d: d.lower.setText("2"))
            assert checks.rules[6].id != old_id
            checks.delete_button.click()
            assert len(checks.rules) == 6
            assert all(r.columns != ("person_id",) for r in checks.rules)
            save_button(checks.save_button, temp / "criteria.json")
            assert len(load_rule_set(temp / "criteria.json")) == 6
            rules = window.rule_panel
            edit_dialog(rules.create_action.trigger, lambda d: (d.column.setCurrentText("person_id"), d.preset.setCurrentIndex(d.preset.findData("numeric_constraint")), d.lower.setText("0")))
            created = next(r for r in rules.rules if r.columns == ("person_id",))
            rules.list.setCurrentRow(rules.visible.index(created))
            edit_dialog(rules.edit_action.trigger, lambda d: d.lower.setText("3"))
            assert all(r.id != created.id for r in rules.rules)
            rules.delete_action.trigger()
            assert all(r.columns != ("person_id",) for r in rules.rules)
            save_button(rules.save_button, temp / "accepted-rules.json")
            open_button(rules.open_button, temp / "accepted-rules.json")
            assert all(r.review_state == "accepted" for r in rules.rules)

            # Cancel file dialogs and malformed JSON preserve committed state.
            old_baseline, old_rules = passport.baseline, list(checks.rules)
            for button in (passport.open_button, checks.open_button, review.open_button):
                open_button(button, "")
            with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
                for button in (passport.save_button, checks.save_button, review.save_button, rules.save_button):
                    button.click()
            malformed = temp / "malformed.json"
            malformed.write_text('{"version": 999}', encoding="utf-8")
            with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as warnings:
                for button in (passport.open_button, checks.open_button, review.open_button):
                    open_button(button, malformed)
                assert warnings.call_count == 3
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(temp / "absent" / "output.json"), "")), \
                    patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as warnings:
                for button in (passport.save_button, checks.save_button, review.save_button, rules.save_button):
                    button.click()
                assert warnings.call_count == 4
            assert passport.baseline == old_baseline and checks.rules == old_rules
            # Source changes retain specs and baseline but discard old row evidence.
            other = temp / "other.csv"
            other.write_text("other\nx\n", encoding="utf-8")
            window.select_path(other)
            assert checks.result is None and rules.result is None and passport.current is None
            assert checks.rules == old_rules and passport.baseline == old_baseline
            assert not window.affected_button.isEnabled()
            window.start_analysis()
            settle(lambda: checks.result is not None)
            assert all(ev.status == "not_applicable" for ev in checks.result.evaluations)
            assert not any(f.code.startswith("custom:") for f in window.result.findings)
            passport.compare_button.click()
            assert passport.comparison["added"] == ["other"]
            window.section_picker.setCurrentIndex(5)
            capture(window, "passport-changed-compact")
            window.resize(1400, 900)
            capture(window, "passport-changed-wide")
            window.row_limit.setValue(10)
            assert not window.rule_source_ready() and checks.result is None and passport.current is None
            passport.clear_button.click()
            assert passport.baseline is None
            # The real Windows launcher stays synchronous for all report flags.
            if os.name == "nt":
                def cli(*args, expected_code=0):
                    result = subprocess.run(["cmd.exe", "/d", "/c", "run.cmd", *map(str, args)],
                                            cwd=root, capture_output=True, timeout=40)
                    assert result.returncode == expected_code, (args, result.stderr)
                demo = create_demo(temp / "old-demo", all_formats=True)
                for extension in ("csv", "xlsx", "parquet"):
                    report = temp / (extension + ".json")
                    cli(demo.with_suffix("." + extension), "--html", temp / (extension + ".html"), "--json", report)
                    payload = json.loads(report.read_text(encoding="utf-8"))
                    assert (payload["rows"], payload["columns"], len(payload["findings"])) == (121, 8, 17)
                report = temp / "custom-cli.json"
                cli(root / "examples/custom_checks_demo.csv", "--criteria", root / "examples/custom_criteria.json",
                    "--html", temp / "custom.html", "--json", report, "--save-passport", temp / "cli-passport.json")
                payload = json.loads(report.read_text(encoding="utf-8"))
                actual = {(ev["rule"]["kind"], ev["rule"]["columns"][0]): [r + 1 for r in ev["row_indices"]]
                          for ev in payload["custom_criteria"]["evaluations"]}
                assert actual == expected
                cli(root / "examples/custom_checks_demo.csv", "--html", temp / "comparison.html",
                    "--compare-passport", temp / "cli-passport.json")
                for flag in ("--criteria", "--save-passport", "--compare-passport"):
                    cli(root / "examples/custom_checks_demo.csv", flag, temp / "unused.json", expected_code=2)
            print(f"Extended native workflow OK; platform={app.platformName()}; six exact check results; seven panels at both sizes; modal CRUD; snapshot; combined HTML; stale guards; JSON errors.")
        finally:
            if window.worker and window.worker.isRunning():
                window.worker.cancel_event.set()
                window.worker.wait(10000)
                app.processEvents()
            window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
