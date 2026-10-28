"""Repeatable desktop acceptance check; renders the actual Qt window offscreen."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QElapsedTimer, QSettings, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QPainter
from data_xray.demo import create_demo
from data_xray.ui import MainWindow
from data_xray.dialogs import ImportDialog


def main():
    root = Path(__file__).resolve().parents[1]
    app = QApplication([])
    # Capture the settled popup, not a transient frame of the Windows animation.
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
    # Qt's offscreen platform on Windows starts with an empty font database.
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    settings_directory = TemporaryDirectory(prefix="data-xray-desktop-")
    settings = QSettings(str(Path(settings_directory.name) / "settings.ini"), QSettings.Format.IniFormat)
    def capture_owned_dialog(dialog, target):
        # Native screen capture is blank in some Windows remote sessions.
        # Both layers below are actual Qt widget grabs, at their actual positions.
        pixmap = window.grab()
        painter = QPainter(pixmap)
        position = window.mapFromGlobal(dialog.mapToGlobal(QPoint(0, 0)))
        painter.drawPixmap(position, dialog.grab())
        painter.end()
        pixmap.save(str(root / "docs" / target))

    window = MainWindow(settings=settings)
    window.show()
    app.processEvents()
    window.show_first_run_help()
    app.processEvents()
    assert not window.help_prompt.isWindow()
    window.grab().save(str(root / "docs" / "desktop-help-invite.png"))
    window.help_prompt.reject()
    window.select_path(create_demo(root / "examples", all_formats=False), auto_start=True)
    assert window.loading_overlay.state == "loading"
    window.grab().save(str(root / "docs" / "desktop-loading-wide.png"))
    timer = QElapsedTimer()
    timer.start()
    while (window.worker.isRunning() or not window.analyze_button.isEnabled() or window.loading_overlay.isVisible()) and timer.elapsed() < 20000:
        QTest.qWait(5)
    if window.worker.isRunning() or window.result is None:
        raise RuntimeError("Desktop analysis did not complete")
    app.processEvents()
    target = root / "docs" / "desktop.png"
    target.parent.mkdir(exist_ok=True)
    if not window.grab().save(str(target)):
        raise RuntimeError("Could not save desktop screenshot")
    for index, name in ((1, "profile"), (2, "data")):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(root / "docs" / f"desktop-{name}-wide.png"))
    window.tabs.setCurrentIndex(0)
    window.resize(1000, 680)
    app.processEvents()
    assert window.size().width() == 1000 and window.size().height() == 680, window.size()
    window.grab().save(str(root / "docs" / "desktop-compact.png"))
    window.tabs.setCurrentIndex(2)
    app.processEvents()
    assert window.preview_view.verticalScrollBar().maximum() > 0
    assert window.preview_view.horizontalScrollBar().maximum() > 0
    window.grab().save(str(root / "docs" / "desktop-data.png"))
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(root / "docs" / "desktop-profile.png"))
    window.tabs.setCurrentIndex(0)
    assert window.result.options.iqr_multiplier == 1.5
    assert not window.progress.isVisible()
    tab_rects = [window.tabs.tabBar().tabRect(i) for i in range(window.tabs.count())]
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        assert [window.tabs.tabBar().tabRect(i) for i in range(window.tabs.count())] == tab_rects
    window.tabs.setCurrentIndex(0)
    dialog = ImportDialog(window.path, parent=window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(root / "docs" / "desktop-import.png"))
    capture_owned_dialog(dialog, "desktop-import-context.png")
    dialog.reject()
    for width, height, suffix in ((1000, 680, ""), (1400, 900, "-wide")):
        window.resize(width, height)
        app.processEvents()
        window.help_button.click()
        app.processEvents()
        assert not window.tour.isWindow()
        assert window.tour.parentWidget() is window.centralWidget()
        window.grab().save(str(root / "docs" / f"desktop-tour-context{suffix}.png"))
        if not suffix:
            window.grab().save(str(root / "docs" / "desktop-tour.png"))
        window.tour.go(1)
        app.processEvents()
        window.grab().save(str(root / "docs" / f"desktop-highlight{suffix}.png"))
        window.tour.go(5)
        app.processEvents()
        window.grab().save(str(root / "docs" / f"desktop-tour-last-context{suffix}.png"))
        if not suffix:
            window.grab().save(str(root / "docs" / "desktop-tour-last.png"))
        window.tour.reject()
    window.resize(1000, 680)
    app.processEvents()
    window.start_analysis()
    assert window.loading_overlay.state == "loading"
    window.grab().save(str(root / "docs" / "desktop-loading.png"))
    timer.restart()
    while (window.worker.isRunning() or window.loading_overlay.isVisible()) and timer.elapsed() < 20000:
        QTest.qWait(5)
    print(f"Desktop OK ({app.platformName()}): {window.result.rows} rows x {window.result.columns} columns, {len(window.result.findings)} findings; 1400x900 and 1000x680; screenshot: {target}")
    window.close()
    settings_directory.cleanup()


if __name__ == "__main__":
    main()

