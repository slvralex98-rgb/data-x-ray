"""Exercise the real Rule Miner window; all settings and demo writes are isolated."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings, QStandardPaths, QElapsedTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from data_xray.ui import MainWindow
from data_xray.rules import save_rule_set, load_rule_set


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "docs"
    app = QApplication([])
    with TemporaryDirectory(prefix="rule-miner-window-") as temporary:
        temp = Path(temporary)
        window = MainWindow(settings=QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat))
        window.show()
        app.processEvents()
        # Redirect the application cache used by the actual demo button.
        original = QStandardPaths.writableLocation
        QStandardPaths.writableLocation = lambda *args: str(temp)
        def settle(predicate=lambda: True):
            timer = QElapsedTimer()
            timer.start()
            while (window._busy or window.loading_overlay.isVisible() or not predicate()) and timer.elapsed() < 20000:
                QTest.qWait(10)
            assert not window._busy and predicate()
            app.processEvents()
        def capture(name):
            QTest.qWait(150)
            captured = window.grab()
            assert captured.width() >= window.width() and captured.height() >= window.height()
            assert captured.save(str(out / f"rule-miner-{name}.png"))
        try:
            window.rule_panel.demo_button.click()
            settle(lambda: window.rule_panel.result is not None)
            panel = window.rule_panel
            assert len(panel.rules) == 35
            panel.search.setText("contact")
            rule = next(r for r in panel.visible if r.kind == "string_shape" and r.params == {"format": "email"})
            panel.list.setCurrentRow(panel.visible.index(rule))
            capture("candidates")
            panel.accept_button.click()
            saved = temp / "accepted.json"
            save_rule_set(panel.rules, saved)
            assert len(load_rule_set(saved)) == 1
            capture("accepted")
            window.select_path(temp / "rule-demo" / "rule_miner_changed.csv", auto_start=True)
            settle()
            assert panel.result is None
            window.start_rule_check()
            settle()
            assert panel.result.evaluations[0].row_indices == [0, 22]
            capture("checked")
            panel.violations_button.click()
            capture("violations")
            assert [r[0] for r in window.preview_view.model().rows] == [1, 23]
            window.tabs.setCurrentIndex(4)
            window.resize(1000, 680)
            capture("compact")
            assert window.size().width() == 1000 and window.size().height() == 680
            # QWidget capture is reliable on native Windows even when desktop
            # capture is unavailable or returns a tiny placeholder in a session.
            if app.platformName() == "windows":
                capture("native")
            print(f"Rule Miner window verified; platform={app.platformName()}; screenshots={out}")
        finally:
            QStandardPaths.writableLocation = original
            if window.worker and window.worker.isRunning():
                window.worker.cancel_event.set()
                window.worker.wait(10000)
                app.processEvents()
            window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
