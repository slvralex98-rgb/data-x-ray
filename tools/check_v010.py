"""Native synthetic 0.1.0 acceptance and path-free publication illustration."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEvent, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from data_xray.criteria_ui import CriteriaDialog
from data_xray.rules import load_rule_set
from data_xray.theme import STYLE
from data_xray.ui import MainWindow


def main():
    root = Path(__file__).resolve().parents[1]
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with TemporaryDirectory(prefix='xray-v010-') as temporary:
        temp = Path(temporary)
        settings = QSettings(str(temp / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('help/show_on_startup', False)
        window = MainWindow(settings=settings)
        window.show()
        def settle():
            elapsed = QElapsedTimer()
            elapsed.start()
            while window._busy or window.loading_overlay.isVisible():
                assert elapsed.elapsed() < 30000
                QTest.qWait(10)
            app.processEvents()
        def edit(action, configure):
            completed = []
            def drive():
                dialog = app.activeModalWidget()
                assert isinstance(dialog, CriteriaDialog)
                configure(dialog)
                dialog.accept()
                assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
                completed.append(True)
            QTimer.singleShot(80, drive)
            action()
            assert completed
        def multi(dialog):
            dialog.preset.setCurrentIndex(dialog.preset.findData('unique_multi'))
            for index in range(dialog.unique_columns.count()):
                item = dialog.unique_columns.item(index)
                item.setCheckState(Qt.CheckState.Checked if item.text() in ('batch', 'location') else Qt.CheckState.Unchecked)
        def single(dialog):
            dialog.preset.setCurrentIndex(dialog.preset.findData('unique'))
            dialog.column.setCurrentText('item_code')
        try:
            checks = window.criteria_panel
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(root / 'examples/uniqueness_criteria.json'), '')):
                checks.open_button.click()
            window.select_path(root / 'examples/uniqueness_demo.csv', auto_start=True)
            settle()
            expected = {('item_code',): [0, 2], ('batch', 'location'): [0, 1, 3, 4]}
            assert {ev.rule.columns: ev.row_indices for ev in checks.result.evaluations if ev.rule.kind == 'unique'} == expected
            for panel, add, change, delete in (
                (checks, checks.add_button.click, checks.edit_button.click, checks.delete_button.click),
                (window.rule_panel, window.rule_panel.create_action.trigger, window.rule_panel.edit_action.trigger, window.rule_panel.delete_action.trigger),
            ):
                panel.rules = []
                panel.refresh()
                before = len(panel.rules)
                edit(add, multi)
                assert len(panel.rules) == before + 1
                created = next(r for r in panel.rules if r.columns == ('batch', 'location'))
                visible = getattr(panel, 'visible', panel.rules)
                panel.list.setCurrentRow(visible.index(created))
                edit(change, single)
                assert any(r.columns == ('item_code',) and r.kind == 'unique' for r in panel.rules)
                path = temp / ('criteria.json' if panel is checks else 'rules.json')
                with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
                    panel.save_button.click()
                assert load_rule_set(path)
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
                    panel.open_button.click()
                visible = getattr(panel, 'visible', panel.rules)
                item = next(r for r in visible if r.kind == 'unique' and r.columns == ('item_code',))
                panel.list.setCurrentRow(visible.index(item))
                delete()
                assert len(panel.rules) <= before
            # Re-open the pristine public checks and show their actual evidence.
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(root / 'examples/uniqueness_criteria.json'), '')):
                checks.open_button.click()
            checks.check_button.click()
            settle()
            window.dataset = replace(window.dataset, source='examples/uniqueness_demo.csv')
            window.result = replace(window.result, source='examples/uniqueness_demo.csv')
            window.passport_panel.set_current(window.dataset, window.result)
            window.source_label.setText('uniqueness_demo.csv · CSV · 6 записей · Синтетический пример')
            window.source_label.setToolTip('examples/uniqueness_demo.csv')
            window.resize(1400, 900)
            window.section_picker.setCurrentIndex(6)
            app.processEvents()
            unique_rule = next(r for r in checks.rules if r.kind == 'unique' and len(r.columns) == 2)
            checks.list.setCurrentRow(checks.rules.index(unique_rule))
            QTest.qWait(100)
            assert '0.1.0' in window.version_label.text() and window.version_label.isVisible()
            assert not window.loading_overlay.isVisible()
            output = root / 'docs/public'
            output.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(output / 'overview.png'))
            report = temp / 'report.json'
            completed = subprocess.run(['cmd.exe', '/d', '/c', 'run.cmd', 'examples/uniqueness_demo.csv', '--criteria',
                'examples/uniqueness_criteria.json', '--json', str(report), '--html', str(temp / 'report.html')],
                cwd=root, capture_output=True, timeout=40)
            assert completed.returncode == 0, completed.stderr
            payload = json.loads(report.read_text(encoding='utf-8'))
            assert {tuple(ev['rule']['columns']): ev['row_indices'] for ev in payload['custom_criteria']['evaluations'] if ev['rule']['kind'] == 'unique'} == expected
            html = (temp / 'report.html').read_text(encoding='utf-8')
            assert 'Комбинация значений повторяется' in html and 'duplicate_combination' not in html
            print('Native Windows 0.1.0 acceptance OK: both constructors multi-to-single CRUD/save/reopen; exact uniqueness UI/CLI; HTML/JSON; synthetic screenshot; visible version.')
        finally:
            if window.worker and window.worker.isRunning():
                window.worker.cancel_event.set()
                assert window.worker.wait(10000)
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
