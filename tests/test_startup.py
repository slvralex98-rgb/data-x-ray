"""Exercise the real GUI entry point in a fresh interpreter, including imports."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("with_dataset", [False, True], ids=["empty", "analyze-demo"])
@pytest.mark.parametrize("show_help", [False, True], ids=["quiet", "embedded-help"])
def test_run_gui_in_fresh_process(tmp_path, with_dataset, show_help):
    # Keep this module free of UI imports: cached modules can hide startup errors.
    script = r'''
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QElapsedTimer, QSettings, QTimer
from PySide6.QtWidgets import QApplication
from data_xray.demo import create_demo
from data_xray import ui
from data_xray.ui import MainWindow, run_gui
from data_xray.theme import DOCUMENT_STYLE, STYLE

directory = Path(sys.argv[1])
settings = QSettings(str(directory / "settings.ini"), QSettings.Format.IniFormat)
# The explicit organization/application overload always uses native settings on
# Windows, regardless of setDefaultFormat. Inject the explicit INI constructor.
ui.QSettings = lambda *args, **kwargs: settings
settings.setValue("help/show_on_startup", sys.argv[3] == "True")
settings.sync()
path = create_demo(directory, all_formats=False) if sys.argv[2] == "True" else None
app = QApplication([])
elapsed = QElapsedTimer()
elapsed.start()
finished = False

def fail(exc_type, exc_value, tb):
    traceback.print_exception(exc_type, exc_value, tb)
    app.exit(1)

sys.excepthook = fail

def check_window():
    global finished
    try:
        windows = [w for w in app.topLevelWidgets() if isinstance(w, MainWindow)]
        assert len(windows) == 1, "run_gui did not construct the main window"
        window = windows[0]
        assert Path(window.settings.fileName()) == directory / "settings.ini"
        assert window.isVisible()
        assert window.detail.document().defaultStyleSheet() == DOCUMENT_STYLE
        assert window.method_view.document().defaultStyleSheet() == DOCUMENT_STYLE
        assert app.styleSheet() == STYLE
        if sys.argv[3] == "True":
            # The startup hook is queued after constructing/showing the window;
            # an already overdue polling timer may run before that zero timer.
            if window.help_prompt is None:
                assert elapsed.elapsed() < 5000, "startup help did not appear"
                return
            assert window.help_prompt is not None and window.help_prompt.isVisible()
            assert not window.help_prompt.isWindow()
            assert window.help_prompt.parentWidget() is window.centralWidget()
            assert window.help_prompt not in app.topLevelWidgets()
            assert [w for w in app.topLevelWidgets() if w.isVisible()] == [window]
        if path:
            assert window.worker is not None, "run_gui did not start analysis"
            if window.worker.isRunning() or not window.analyze_button.isEnabled():
                assert elapsed.elapsed() < 20000, "GUI analysis timed out"
                return
            assert window.result is not None, "GUI analysis failed"
            result = window.result
            assert (result.rows, result.columns, len(result.findings)) == (121, 8, 17)
            assert window.export_button.isEnabled()
        else:
            assert window.worker is None and window.result is None
        finished = True
        window.close()
        app.quit()
    except BaseException:
        timer.stop()
        fail(*sys.exc_info())

timer = QTimer()
timer.timeout.connect(check_window)
timer.start(10)
exit_code = run_gui(str(path) if path else None)
assert finished, "GUI exited before startup verification completed"
raise SystemExit(exit_code)
'''
    environment = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen"), "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(with_dataset), str(show_help)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=40,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
