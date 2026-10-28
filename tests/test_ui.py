import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QElapsedTimer, QTimer, QSettings, Qt, QPoint
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QMessageBox, QPushButton, QStyle, QStyleOptionSpinBox, QLabel
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
import pytest

from data_xray.demo import create_demo
from data_xray.ui import MainWindow
from data_xray.dialogs import ImportDialog, TourDialog
from data_xray.report import render_report


@pytest.fixture
def ui(tmp_path):
    app = QApplication.instance() or QApplication([])
    if os.name == 'nt' and not app.property('testFontsLoaded'):
        for name in ('segoeui.ttf', 'segoeuib.ttf'):
            font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        app.setProperty('testFontsLoaded', True)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.show()
    app.processEvents()
    yield window
    for dialog in (window.tour, window.help_prompt):
        if dialog and dialog.isVisible():
            dialog.reject()
    if window.worker and window.worker.isRunning():
        window.worker.cancel_event.set()
        assert window.worker.wait(5000)
        app.processEvents()
    window.close()
    app.processEvents()


def test_desktop_worker_filters_and_drilldown(tmp_path, ui):
    app = QApplication.instance()
    window = ui
    path = create_demo(tmp_path, all_formats=False)
    window.select_path(path)
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    wait_analysis(window)
    assert window.result is not None
    assert window.result.rows == 121
    assert window.export_button.isEnabled()
    window.search.setText("amount")
    assert window.visible_findings
    assert all("amount" in f.columns for f in window.visible_findings)
    window.show_affected()
    assert window.tabs.currentIndex() == 2
    assert window.preview_view.model().rowCount() > 0
    window.reset_preview_button.click()
    assert window.preview_view.model().rowCount() == 121
    window.search.setText("not-a-real-column")
    assert len(window.visible_findings) == 0
    window.close()


def wait_until(predicate, timeout=5000):
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout:
        QTest.qWait(5)
    assert predicate(), "Timed out waiting for Qt state"


def wait_analysis(window):
    # finished may already have fired before the caller connects to it.
    wait_until(lambda: not window.worker.isRunning() and window.analyze_button.isEnabled(), 20000)
    QApplication.instance().processEvents()


def test_import_accept_cancel_and_reconfigure(tmp_path, monkeypatch, ui):
    previous = create_demo(tmp_path / "previous", all_formats=False)
    ui.select_path(previous)
    ui.analyze_button.click()
    wait_analysis(ui)
    previous_result = ui.result
    ui.separator.setCurrentIndex(2)
    old_options = ui.load_options()
    chosen = tmp_path / "chosen.csv"
    chosen.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(chosen), ""))
    def reject_import():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        dialog.separator.setCurrentIndex(1)
        QTest.mouseClick(dialog.buttons.buttons()[1], Qt.MouseButton.LeftButton)
    click_modal(ui.open_button, reject_import)
    assert ui.path == str(previous)
    assert ui.result is previous_result
    assert ui.load_options() == old_options
    def accept_import():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        dialog.separator.setCurrentIndex(1)
        QTest.mouseClick(dialog.accept_button, Qt.MouseButton.LeftButton)
    click_modal(ui.open_button, accept_import)
    assert ui.path == str(chosen)
    assert ui.load_options()["separator"] == ","
    click_modal(ui.import_button, reject_import)
    assert ui.load_options()["separator"] == ","


def test_import_fields_for_each_format(tmp_path, ui):
    path = create_demo(tmp_path)
    for suffix in (".csv", ".xlsx", ".parquet"):
        dialog = ImportDialog(path.with_suffix(suffix), parent=ui)
        dialog.show()
        QApplication.instance().processEvents()
        assert dialog.separator.isVisible() == (suffix == ".csv")
        assert dialog.encoding.isVisible() == (suffix == ".csv")
        assert dialog.sheet.isVisible() == (suffix == ".xlsx")
        if suffix == ".xlsx":
            assert dialog.sheet.count() >= 2
        dialog.reject()


def test_analysis_controls_reach_result_and_method(tmp_path, ui):
    ui.select_path(create_demo(tmp_path, all_formats=False))
    ui.example_rows.setValue(2)
    assert not hasattr(ui, "outlier_sensitivity")
    ui.analyze_button.click()
    assert not ui.import_button.isEnabled()
    wait_analysis(ui)
    assert ui.result.options.example_rows == 2
    assert ui.result.options.iqr_multiplier == 1.5
    assert all(len(f.examples) <= 2 for f in ui.result.findings)
    assert "1.5 × IQR" in ui.method_view.toPlainText()
    assert "до 2 примеров" in ui.method_view.toPlainText()
    assert "До 2 примеров" in render_report(ui.result)


@pytest.mark.parametrize("size", [(1400, 900), (1000, 680)])
def test_chrome_frames_tabs_and_spin_text_insets(ui, tmp_path, size):
    ui.resize(*size)
    analyze_demo(ui, tmp_path)
    bar = ui.tabs.tabBar()
    rects = [bar.tabRect(i) for i in range(ui.tabs.count())]
    for index in range(ui.tabs.count()):
        QTest.mouseMove(bar, bar.tabRect(index).center())
        ui.tabs.setCurrentIndex(index)
        QApplication.instance().processEvents()
        # Seven sections may scroll in a compact window; font metrics stay fixed.
        assert [bar.tabRect(i).size() for i in range(ui.tabs.count())] == [r.size() for r in rects]
        assert bar.tabRect(index).intersects(bar.rect())
        assert ui.section_picker.currentIndex() == index
    for table, index in ((ui.profile_view, 1), (ui.preview_view, 2)):
        ui.tabs.setCurrentIndex(index)
        QApplication.instance().processEvents()
        frame = table.parentWidget()
        assert frame.objectName() == "tableFrame"
        assert frame.rect().adjusted(6, 6, -6, -6).contains(table.geometry())
        assert table.horizontalScrollBar().isVisible()
        assert table.horizontalScrollBar().maximum() > 0
        # Sample the actual painted straight edges, outside headers/viewport.
        for outline in (frame, ui.tabs_frame):
            image = outline.grab().toImage()
            for x, y in ((image.width() // 2, 0), (0, image.height() // 2),
                         (image.width() - 1, image.height() // 2),
                         (image.width() // 2, image.height() - 1)):
                assert image.pixelColor(x, y).name() == "#df8548"
    for spin in (ui.row_limit, ui.example_rows):
        edit = spin.lineEdit()
        assert edit.textMargins().left() >= 10
        assert edit.textMargins().right() >= 8
        spin.setValue(spin.maximum())
        assert edit.fontMetrics().horizontalAdvance(edit.text()) < edit.width() - 18
    ui.progress.setValue(100)
    assert not ui.progress.isVisible()
    assert ui.centralWidget().layout().indexOf(ui.progress) == -1
    assert not hasattr(ui, "outlier_sensitivity")
    labels = " ".join(label.text().casefold() for label in ui.findChildren(QLabel))
    for removed in ("чувствительность", "локально", "файл не измен", "проблемы и подсказки в данных"):
        assert removed not in labels


def test_tour_navigation_preferences_and_startup(ui, tmp_path):
    assert ui.tour is None and ui.help_prompt is None
    ui.tabs.setCurrentIndex(2)
    QTest.mouseClick(ui.help_button, Qt.MouseButton.LeftButton)
    tour = ui.tour
    assert isinstance(tour, TourDialog) and tour.isVisible()
    assert not tour.back_button.isEnabled()
    tour.next_button.click()
    assert tour.step == 1
    tour.back_button.click()
    assert tour.step == 0
    for _ in range(len(tour.steps) - 1):
        tour.next_button.click()
    assert tour.counter.text() == f"Шаг {len(tour.steps)} из {len(tour.steps)}"
    assert tour.next_button.text() == "Готово"
    tour.auto_show.setChecked(False)
    tour.next_button.click()
    assert not tour.isVisible()
    assert ui.tabs.currentIndex() == 2
    persisted = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert persisted.value("help/show_on_startup", True, type=bool) is False
    ui.show_first_run_help()
    assert ui.help_prompt is None
    ui.help_button.click()
    assert ui.tour.isVisible()
    ui.tour.close_button.click()
    ui.settings.setValue("help/show_on_startup", True)
    ui.show_first_run_help()
    ui.help_prompt.auto_show.setChecked(False)
    ui.help_prompt.accept()
    assert ui.tour.isVisible()
    assert not ui.tour.auto_show.isChecked()
    ui.tour.reject()


def test_demo_resets_csv_settings(tmp_path, monkeypatch, ui):
    monkeypatch.setattr("data_xray.ui.create_demo", lambda *a, **kw: create_demo(tmp_path, all_formats=False))
    ui.separator.setCurrentIndex(4)
    ui.encoding.setCurrentIndex(1)
    ui.demo_button.click()
    wait_analysis(ui)
    assert ui.result.rows == 121
    assert ui.load_options() == {"separator": None, "encoding": "utf-8-sig", "sheet": None}


def test_compact_sidebar_and_tour_geometry(ui):
    ui.resize(1000, 680)
    QApplication.instance().processEvents()
    assert ui.width() == 1000 and ui.height() == 680
    panel = ui.analysis_panel
    assert panel.height() >= panel.sizeHint().height()
    previous_bottom = -1
    for i in range(panel.layout().count()):
        control = panel.layout().itemAt(i).widget()
        assert control.geometry().top() > previous_bottom
        previous_bottom = control.geometry().bottom()
        assert panel.rect().contains(control.geometry())
    ui.help_button.click()
    for _ in range(len(ui.tour.steps)):
        QApplication.instance().processEvents()
        assert ui.centralWidget().rect().contains(ui.tour.geometry())
        ui.tour.next_button.click()


@pytest.mark.parametrize("size", [(1000, 680), (1400, 900)])
def test_guidance_is_embedded_anchored_and_tracks_resize(ui, size):
    ui.resize(*size)
    QApplication.instance().processEvents()
    windows = set(QApplication.topLevelWidgets())
    ui.show_first_run_help()
    prompt = ui.help_prompt
    assert prompt.parentWidget() is ui.centralWidget() and not prompt.isWindow()
    assert not isinstance(prompt, QDialog)
    assert ui.centralWidget().rect().contains(prompt.geometry())
    assert set(QApplication.topLevelWidgets()) == windows
    ui.help_button.click()
    assert not prompt.isVisible() and not prompt.escape.isEnabled()
    tour = ui.tour
    assert tour.parentWidget() is ui.centralWidget() and not tour.isWindow()
    assert not isinstance(tour, QDialog)
    assert set(QApplication.topLevelWidgets()) == windows
    for step in range(len(tour.steps)):
        tour.go(step - tour.step)
        QTest.qWait(5)
        assert ui.centralWidget().rect().contains(tour.geometry())
        target = tour.target_rect()
        assert tour.highlight.geometry() == target.adjusted(-3, -3, 3, 3)
        assert tour.geometry().adjusted(-35, -35, 35, 35).intersects(target)
        if step in (0, 1, 2, 6):
            assert not tour.geometry().intersects(target)
    ui.resize(1400 if size[0] == 1000 else 1000, 680 if size[0] == 1400 else 900)
    QTest.qWait(20)
    assert ui.centralWidget().rect().contains(tour.geometry())
    assert tour.highlight.geometry() == tour.target_rect().adjusted(-3, -3, 3, 3)
    # Escape works even after focus has moved back into the application.
    ui.help_button.setFocus()
    QTest.keyClick(ui.help_button, Qt.Key.Key_Escape)
    assert not tour.isVisible() and not tour.highlight.isVisible() and not tour.pointer.isVisible()
    assert not tour.reposition_timer.isActive()


def test_loader_fast_success_atomic_models_and_repeat_during_fade(ui, tmp_path):
    ui.select_path(create_demo(tmp_path, all_formats=False))
    ui.start_analysis()
    overlay = ui.loading_overlay
    assert overlay.isVisible() and not overlay.isWindow()
    assert overlay.parentWidget() is ui.centralWidget()
    assert overlay.state == "loading" and overlay.effect.opacity() == 1
    wait_analysis(ui)
    assert ui.result.rows == 121 and overlay.isVisible()
    assert overlay.state in ("settling", "fading")
    profile, preview = ui.profile_view.model(), ui.preview_view.model()
    wait_until(lambda: overlay.state == "fading")
    old_generation = overlay.generation
    ui.row_limit.setValue(20)
    ui.start_analysis()
    assert overlay.generation > old_generation and overlay.effect.opacity() == 1
    assert overlay.state == "loading"
    wait_analysis(ui)
    assert ui.result.rows == 20
    assert ui.profile_view.model() is profile and ui.preview_view.model() is preview
    assert preview.rowCount() == 20
    ui.resize(1000, 680)
    QApplication.instance().processEvents()
    assert overlay.geometry() == ui.tabs_frame.geometry()
    wait_until(lambda: not overlay.isVisible())
    assert overlay.state == "idle" and not overlay.pulse.isActive()


def test_fast_loader_minimum_visibility_fade_and_event_loop(ui, tmp_path):
    ui.resize(1000, 680)
    QApplication.instance().processEvents()
    beats = []
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: beats.append(True))
    heartbeat.start(10)
    try:
        ui.select_path(create_demo(tmp_path, all_formats=False))
        ui.start_analysis()
        overlay = ui.loading_overlay
        wait_analysis(ui)
        finished_at = overlay.elapsed.elapsed()
        assert ui.analyze_button.isEnabled() and overlay.isVisible()
        wait_until(lambda: overlay.state == "fading")
        assert overlay.elapsed.elapsed() >= 200  # Allow timer resolution tolerance.
        assert overlay.fade.duration() == 210
        wait_until(lambda: not overlay.isVisible())
        assert overlay.elapsed.elapsed() >= max(210, finished_at) + 190
        assert len(beats) >= 8  # Qt continues dispatching during settling/fade.
    finally:
        heartbeat.stop()


@pytest.mark.parametrize("previous_state", ["settling", "fading"])
def test_previous_loader_timers_cannot_hide_new_slow_worker(ui, tmp_path, monkeypatch, previous_state):
    from threading import Event
    from data_xray.ui import Analyzer
    from data_xray.checks import AnalysisCancelled
    analyze_demo(ui, tmp_path)
    overlay = ui.loading_overlay
    # Isolate callback overlap from machine-dependent analysis duration. A long
    # analysis legitimately has a zero settling delay and can already be fading.
    # Re-arm the completed workspace overlay with a fresh elapsed clock so both
    # previous timer states are reached deterministically.
    overlay.begin(ui.tabs_frame)
    overlay.finish()
    wait_until(lambda: overlay.state == previous_state)
    started = Event()
    def controlled(self, dataset, options, progress, cancel):
        started.set()
        cancel.wait(5)
        raise AnalysisCancelled()
    monkeypatch.setattr(Analyzer, "analyze", controlled)
    click(ui.analyze_button)
    wait_until(started.is_set)
    # The complete previous delay + fade expires while this analysis is active.
    QTest.qWait(450)
    assert ui.worker.isRunning() and overlay.state == "loading"
    assert overlay.isVisible() and overlay.effect.opacity() == 1
    assert overlay.pulse.isActive() and ui.cancel_button.isEnabled()
    click(ui.cancel_button)
    wait_analysis(ui)
    wait_until(lambda: not overlay.isVisible())


def test_tour_remains_clickable_during_analysis(ui, tmp_path, monkeypatch):
    from threading import Event
    from data_xray.ui import Analyzer
    from data_xray.checks import AnalysisCancelled
    started = Event()
    def controlled(self, dataset, options, progress, cancel):
        started.set()
        cancel.wait(5)
        raise AnalysisCancelled()
    monkeypatch.setattr(Analyzer, "analyze", controlled)
    ui.select_path(create_demo(tmp_path, all_formats=False))
    ui.show_tour()
    ui.tour.go(2)
    click(ui.analyze_button)
    wait_until(started.is_set)
    assert ui.loading_overlay.isVisible()
    point = ui.tour.next_button.mapTo(ui.centralWidget(), ui.tour.next_button.rect().center())
    assert ui.centralWidget().childAt(point) is ui.tour.next_button
    click(ui.tour.next_button)
    assert ui.tour.step == 3
    ui.tour.reject()
    ui.cancel_analysis()
    wait_analysis(ui)


@pytest.mark.parametrize("outcome", ["fail", "cancel"])
def test_loader_preserves_previous_models_on_failure_and_cancel(ui, tmp_path, monkeypatch, outcome):
    from threading import Event
    from data_xray.ui import Analyzer
    from data_xray.checks import AnalysisCancelled
    previous = analyze_demo(ui, tmp_path)
    profile, preview = ui.profile_view.model(), ui.preview_view.model()
    original_rows = list(preview.rows)
    release, started = Event(), Event()
    def controlled(self, dataset, options, progress, cancel):
        progress(35, "Проверка столбцов…")
        started.set()
        release.wait(5)
        if cancel.is_set():
            raise AnalysisCancelled()
        raise ValueError("controlled failure")
    monkeypatch.setattr(Analyzer, "analyze", controlled)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    ui.start_analysis()
    wait_until(started.is_set)
    wait_until(lambda: "35%" in ui.loading_overlay.stage.text())
    assert ui.loading_overlay.isVisible() and ui.cancel_button.isEnabled()
    assert ui.result is previous and preview.rows == original_rows
    if outcome == "cancel":
        ui.cancel_analysis()
        assert "Отмена" in ui.loading_overlay.stage.text()
    release.set()
    wait_analysis(ui)
    assert ui.result is previous
    assert ui.profile_view.model() is profile and ui.preview_view.model() is preview
    assert preview.rows == original_rows
    wait_until(lambda: not ui.loading_overlay.isVisible())
    assert ui.close() and not ui.loading_overlay.delay.isActive()


def click(widget):
    assert widget.isVisible() and widget.isEnabled()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    QApplication.instance().processEvents()


def click_modal(button, action):
    # Report assertion failures in a timer callback without stranding exec().
    errors = []
    def perform():
        dialog = QApplication.activeModalWidget()
        try:
            action()
        except BaseException as exc:
            errors.append(exc)
            if dialog:
                dialog.reject()
    QTimer.singleShot(0, perform)
    click(button)
    if errors:
        raise errors[0]


def named_button(dialog, text):
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)


def choose_combo(combo, index):
    # Exercise both the drop-down arrow and selection in the actual popup.
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=QPoint(combo.width() - 12, combo.height() // 2))
    view = combo.view()
    wait_until(view.isVisible)  # Native Windows may animate the popup.
    # Small styled choice lists must expose every entry.
    # All entries in these small, styled lists must be readable without scrolling.
    for row in range(combo.count()):
        rect = view.visualRect(combo.model().index(row, 0))
        assert 0 <= rect.top() and rect.bottom() < view.viewport().height()
    QTest.keyClick(view, Qt.Key.Key_Home)
    for _ in range(index):
        QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert combo.currentIndex() == index


def analyze_demo(ui, tmp_path):
    ui.select_path(create_demo(tmp_path, all_formats=False))
    click(ui.analyze_button)
    wait_analysis(ui)
    assert (ui.result.rows, ui.result.columns, len(ui.result.findings)) == (121, 8, 17)
    return ui.result


def test_initial_buttons_and_cancel_file_picker(ui, monkeypatch):
    assert ui.open_button.isEnabled() and ui.demo_button.isEnabled() and ui.help_button.isEnabled()
    for button in (ui.analyze_button, ui.cancel_button, ui.import_button, ui.export_button,
                   ui.affected_button, ui.reset_preview_button):
        assert not button.isEnabled()
    calls = []
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (calls.append(kw) or ('', '')))
    click(ui.open_button)
    assert len(calls) == 1 and calls[0]['options'] == QFileDialog.Option.DontUseNativeDialog
    assert ui.path is None and ui.result is None


@pytest.mark.parametrize('dismiss', ['cancel', 'close', 'escape'])
def test_import_dismissal_preserves_previous_dataset(ui, tmp_path, monkeypatch, dismiss):
    previous = analyze_demo(ui, tmp_path / 'old')
    old_path, old_options = ui.path, ui.load_options()
    chosen = tmp_path / 'new.csv'
    chosen.write_text('x;y\n1;2\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(chosen), ''))
    def dismiss_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ImportDialog)
        choose_combo(dialog.separator, 2)
        if dismiss == 'cancel':
            click(named_button(dialog, 'Отмена'))
        elif dismiss == 'escape':
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        else:
            assert dialog.close()  # Same QCloseEvent path as the native title-bar cross.
    click_modal(ui.open_button, dismiss_dialog)
    assert (ui.path, ui.load_options()) == (old_path, old_options)
    assert ui.result is previous and ui.dataset.frame.shape == (121, 8)


def test_reconfigure_csv_and_xlsx_sheet_through_widgets(ui, tmp_path, monkeypatch):
    from openpyxl import Workbook
    csv_path = tmp_path / 'russian.csv'
    csv_path.write_text('код;имя\n001;Иван\n002;Ольга\n', encoding='cp1251')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(csv_path), ''))
    def set_csv():
        dialog = QApplication.activeModalWidget()
        choose_combo(dialog.separator, 2)
        choose_combo(dialog.encoding, 1)
        click(dialog.accept_button)
    click_modal(ui.open_button, set_csv)
    click(ui.analyze_button)
    wait_analysis(ui)
    assert ui.dataset.frame.row(0) == ('001', 'Иван')
    def reconfigure():
        dialog = QApplication.activeModalWidget()
        assert dialog.separator.currentData() == ';' and dialog.encoding.currentData() == 'cp1251'
        click(dialog.accept_button)
    click_modal(ui.import_button, reconfigure)
    book = Workbook()
    book.active.title = 'Первый'
    book.active.append(['a'])
    book.active.append(['first'])
    second = book.create_sheet('Второй')
    second.append(['b'])
    second.append(['second'])
    path = tmp_path / 'two.xlsx'
    book.save(path)
    book.close()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), ''))
    def set_sheet():
        dialog = QApplication.activeModalWidget()
        assert not dialog.separator.isVisible() and not dialog.encoding.isVisible()
        choose_combo(dialog.sheet, 2)
        click(dialog.accept_button)
    click_modal(ui.open_button, set_sheet)
    click(ui.analyze_button)
    wait_analysis(ui)
    assert ui.dataset.frame.columns == ['b'] and ui.dataset.frame.row(0) == ('second',)
    def assert_sheet_and_cancel():
        dialog = QApplication.activeModalWidget()
        assert dialog.sheet.currentData() == 'Второй'
        click(named_button(dialog, 'Отмена'))
    click_modal(ui.import_button, assert_sheet_and_cancel)
    assert ui.load_options()['sheet'] == 'Второй'


def test_parquet_accept_has_no_csv_fields(ui, tmp_path, monkeypatch):
    path = create_demo(tmp_path).with_suffix('.parquet')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), ''))
    def accept():
        dialog = QApplication.activeModalWidget()
        assert not any(c.isVisible() for c in (dialog.separator, dialog.encoding, dialog.sheet))
        click(dialog.accept_button)
    click_modal(ui.open_button, accept)
    click(ui.analyze_button)
    wait_analysis(ui)
    assert (ui.result.rows, ui.result.columns, len(ui.result.findings)) == (121, 8, 17)


def test_export_confirm_cancel_and_failure(ui, tmp_path, monkeypatch):
    result = analyze_demo(ui, tmp_path / 'source')
    ui.search.setText('amount')
    path = tmp_path / 'confirmed'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(path), ''))
    click(ui.export_button)
    report = path.with_suffix('.html').read_text(encoding='utf-8')
    assert report == render_report(result, review=ui.finding_review.review,
                                   passport=ui.passport_panel.export_content())
    assert all(f.title in report for f in result.findings)
    saved_status = ui.statusBar().currentMessage()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: ('', ''))
    click(ui.export_button)
    assert ui.statusBar().currentMessage() == saved_status
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[1:]))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(tmp_path / 'missing' / 'report.html'), ''))
    click(ui.export_button)
    assert warnings[0][0] == 'Ошибка экспорта' and warnings[0][1]
    assert ui.result is result and ui.export_button.isEnabled()


def test_read_failures_preserve_previous_result(ui, tmp_path, monkeypatch):
    previous = analyze_demo(ui, tmp_path / 'old')
    dataset = ui.dataset
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[1:]))
    broken = tmp_path / 'broken.xlsx'
    broken.write_bytes(b'invalid workbook')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(broken), ''))
    old_path = ui.path
    click(ui.open_button)
    assert warnings[-1][0] == 'Не удалось прочитать книгу' and ui.path == old_path
    ui.select_path(tmp_path / 'missing.csv')
    click(ui.analyze_button)
    wait_analysis(ui)
    assert warnings[-1][0] == 'Не удалось выполнить анализ'
    assert 'Предыдущий результат сохранен' in ui.statusBar().currentMessage()
    assert ui.result is previous and ui.dataset is dataset
    assert ui.export_button.isEnabled() and not ui.cancel_button.isEnabled()


@pytest.mark.parametrize('close_window', [False, True])
def test_busy_cancel_close_and_rerun(ui, tmp_path, monkeypatch, close_window):
    from threading import Event
    from data_xray.ui import Analyzer
    from data_xray.checks import AnalysisCancelled
    previous = analyze_demo(ui, tmp_path)
    dataset = ui.dataset
    started = Event()
    original = Analyzer.analyze
    def controlled_analysis(self, dataset, options, progress, cancel):
        started.set()
        if not cancel.wait(5):
            raise RuntimeError('test did not cancel worker')
        raise AnalysisCancelled()
    monkeypatch.setattr(Analyzer, 'analyze', controlled_analysis)
    click(ui.analyze_button)
    wait_until(started.is_set)
    worker = ui.worker
    for control in (ui.open_button, ui.demo_button, ui.import_button, ui.analyze_button,
                    ui.export_button, ui.row_limit, ui.example_rows):
        assert not control.isEnabled()
    assert ui.help_button.isEnabled() and ui.cancel_button.isEnabled()
    QTest.mouseClick(ui.analyze_button, Qt.MouseButton.LeftButton)
    assert ui.worker is worker
    if close_window:
        assert not ui.close() and ui.isVisible()
    else:
        click(ui.cancel_button)
        assert not ui.cancel_button.isEnabled()
    wait_analysis(ui)
    assert worker.cancel_event.is_set()
    assert ui.result is previous and ui.dataset is dataset
    assert ui.progress.value() == 0
    assert 'отменен' in ui.statusBar().currentMessage()
    assert ui.export_button.isEnabled()
    monkeypatch.setattr(Analyzer, 'analyze', original)
    ui.row_limit.setValue(20)
    click(ui.analyze_button)
    wait_analysis(ui)
    assert ui.result is not previous and ui.result.rows == 20 and ui.result.truncated
    assert 'ТОЛЬКО ПЕРВЫЕ 20' in ui.source_label.text()
    assert ui.close() and not ui.isVisible()


@pytest.mark.parametrize('dismiss', ['close_button', 'cross', 'escape', 'done'])
def test_tour_all_exit_paths_restore_state(ui, tmp_path, dismiss):
    previous = analyze_demo(ui, tmp_path)
    ui.tabs.setCurrentIndex(2)
    ui.search.setText('amount')
    before = (ui.path, ui.load_options(), ui.search.text(), ui.row_limit.value())
    click(ui.help_button)
    tour = ui.tour
    assert tour.step == 0 and not tour.back_button.isEnabled()
    QTest.mouseClick(tour.back_button, Qt.MouseButton.LeftButton)
    assert tour.step == 0
    click(tour.next_button)
    click(tour.back_button)
    assert tour.step == 0
    for step in range(1, len(tour.steps)):
        click(tour.next_button)
        assert tour.step == step and tour.counter.text() == f'Шаг {step + 1} из {len(tour.steps)}'
    assert tour.next_button.text() == 'Готово' and ui.tabs.currentIndex() == 3
    click(tour.auto_show)
    assert not tour.auto_show.isChecked()
    if dismiss == 'close_button':
        click(tour.close_button)
    elif dismiss == 'cross':
        assert tour.close()
    elif dismiss == 'escape':
        QTest.keyClick(tour, Qt.Key.Key_Escape)
    else:
        click(tour.next_button)
        assert tour.result() == QDialog.DialogCode.Accepted
    assert not tour.isVisible() and ui.tabs.currentIndex() == 2
    assert (ui.path, ui.load_options(), ui.search.text(), ui.row_limit.value()) == before
    assert ui.result is previous
    assert not QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat).value('help/show_on_startup', True, type=bool)
    wait_until(lambda: not any(frame.isVisible() for frame in ui.centralWidget().findChildren(type(tour.highlight)) if frame is tour.highlight))


@pytest.mark.parametrize('action', ['Показать', 'Не сейчас', 'escape', 'cross'])
def test_first_help_prompt_persistence_and_manual_tour(ui, tmp_path, action):
    ui.show_first_run_help()
    prompt = ui.help_prompt
    assert prompt.isVisible()
    click(prompt.auto_show)
    if action in ('Показать', 'Не сейчас'):
        click(named_button(prompt, action))
    elif action == 'escape':
        QTest.keyClick(prompt, Qt.Key.Key_Escape)
    else:
        prompt.close()
    assert not prompt.isVisible()
    assert ui.settings.value('help/show_on_startup', True, type=bool) is False
    if action == 'Показать':
        assert ui.tour.isVisible() and not ui.tour.auto_show.isChecked()
        click(ui.tour.close_button)
    else:
        assert ui.tour is None
    ui.show_first_run_help()
    assert ui.help_prompt is prompt and not prompt.isVisible()
    click(ui.help_button)
    assert ui.tour.isVisible() and not ui.tour.auto_show.isChecked()
    click(ui.tour.close_button)
    persisted = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    assert persisted.value('help/show_on_startup', True, type=bool) is False


def test_spinners_combo_filters_tabs_drilldown_and_scroll(ui, tmp_path):
    ui.resize(1000, 680)
    QApplication.instance().processEvents()
    for spin in (ui.row_limit, ui.example_rows):
        old = spin.value()
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        for subcontrol, expected in ((QStyle.SubControl.SC_SpinBoxUp, old + spin.singleStep()),
                                     (QStyle.SubControl.SC_SpinBoxDown, old)):
            rect = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, subcontrol, spin)
            assert not rect.isEmpty()
            QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=rect.center())
            assert spin.value() == expected
    analyze_demo(ui, tmp_path)
    assert ui.result.options.iqr_multiplier == 1.5
    assert [label.text() for label in ui.metrics] == ['121', '8', '4', '17']
    for priority in ('high', 'medium', 'low', 'info'):
        choose_combo(ui.severity, ui.severity.findData(priority))
        assert ui.visible_findings == [f for f in ui.result.findings if f.severity == priority]
    choose_combo(ui.severity, ui.severity.findData('high'))
    assert ui.visible_findings and all(f.severity == 'high' for f in ui.visible_findings)
    ui.search.setFocus()
    QTest.keyClicks(ui.search, 'amount')
    assert ui.visible_findings and all('amount' in f.columns for f in ui.visible_findings)
    QTest.keyClick(ui.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(ui.search, Qt.Key.Key_Backspace)
    choose_combo(ui.severity, 0)
    index = next(i for i, f in enumerate(ui.visible_findings) if f.code == 'duplicates')
    item = ui.findings_list.item(index)
    ui.findings_list.scrollToItem(item)
    QTest.mouseClick(ui.findings_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=ui.findings_list.visualItemRect(item).center())
    assert 'лишних копий' in ui.detail.toPlainText()
    click(ui.affected_button)
    assert ui.tabs.currentIndex() == 2
    model = ui.preview_view.model()
    assert model.rows[0][0] == 1 and model.rows[1][0] == 121
    click(ui.reset_preview_button)
    assert ui.preview_view.model().rowCount() == 121
    for index in (1, 2, 3, 0):
        QTest.mouseClick(ui.tabs.tabBar(), Qt.MouseButton.LeftButton,
                         pos=ui.tabs.tabBar().tabRect(index).center())
        assert ui.tabs.currentIndex() == index
    for area in (ui.findings_list, ui.detail):
        scroll = area.verticalScrollBar()
        if scroll.maximum() > 0:
            scroll.setFocus()
            QTest.keyClick(scroll, Qt.Key.Key_End)
            assert scroll.value() == scroll.maximum()
            QTest.keyClick(scroll, Qt.Key.Key_Home)
            assert scroll.value() == 0
    QTest.mouseClick(ui.tabs.tabBar(), Qt.MouseButton.LeftButton, pos=ui.tabs.tabBar().tabRect(2).center())
    for scroll in (ui.preview_view.horizontalScrollBar(), ui.preview_view.verticalScrollBar()):
        assert scroll.isVisible() and scroll.maximum() > 0
        scroll.setFocus()
        QTest.keyClick(scroll, Qt.Key.Key_End)
        assert scroll.value() == scroll.maximum()
        QTest.keyClick(scroll, Qt.Key.Key_Home)
        assert scroll.value() == 0
    click(ui.reset_preview_button)
    ui.tabs.setCurrentIndex(0)
    ui.search.setText('no-result-for-this-query')
    assert not ui.visible_findings and not ui.affected_button.isEnabled()
    assert 'Нет результатов' in ui.detail.toPlainText()
