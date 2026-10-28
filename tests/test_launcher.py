"""Exercise the batch file, real pythonw process and console error reporting."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from data_xray import gui_entry, launcher


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_launcher(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows batch/pythonw integration")
    root = tmp_path / "Проект с пробелами & данными"
    scripts = root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    source = ROOT / ".venv"
    for filename in ("python.exe", "pythonw.exe"):
        shutil.copy2(source / "Scripts" / filename, scripts / filename)
    shutil.copy2(source / "pyvenv.cfg", root / ".venv" / "pyvenv.cfg")
    shutil.copy2(ROOT / "run.cmd", root / "run.cmd")
    package = root / "data_xray"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for filename in ("launcher.py", "gui_entry.py"):
        shutil.copy2(ROOT / "data_xray" / filename, package / filename)
    (package / "__main__.py").write_text('''
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time

def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?")
    parser.add_argument("--html")
    parser.add_argument("--json")
    parser.add_argument("--sheet")
    return parser

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    Path("started.json").write_text(json.dumps({
        "argv": args, "cwd": str(Path.cwd()), "pid": os.getpid(),
        "console": bool(ctypes.windll.kernel32.GetConsoleWindow()),
        "executable": sys.executable,
    }, ensure_ascii=False), encoding="utf-8")
    if "--html" in args or "--json" in args:
        print("CLI completed", flush=True)
        return 7
    deadline = time.monotonic() + 8
    while not Path("release.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    Path("finished.txt").write_text("done", encoding="utf-8")
    return 0
''', encoding="utf-8")
    return root


def run_batch(root, arguments):
    command = subprocess.list2cmdline([str(root / "run.cmd"), *arguments])
    return subprocess.run(
        f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{command}"',
        cwd=root.parent, capture_output=True, timeout=12,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def wait_for(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.05)
    pytest.fail(f"Child did not produce {path.name}")


def test_batch_detaches_pythonw_and_preserves_arguments(isolated_launcher):
    root = isolated_launcher
    arguments = ["данные с пробелами & символами.csv", "--sheet", "Лист номер 2"]
    try:
        completed = run_batch(root, arguments)
        assert completed.returncode == 0, completed.stderr
        wait_for(root / "started.json")
        recorded = json.loads((root / "started.json").read_text(encoding="utf-8"))
        assert recorded["argv"] == arguments
        assert Path(recorded["cwd"]) == root
        assert Path(recorded["executable"]).name.lower() == "pythonw.exe"
        assert recorded["console"] is False
        # The batch file has exited while its desktop child is still alive.
        assert not (root / "finished.txt").exists()
    finally:
        (root / "release.txt").write_text("release", encoding="utf-8")
    wait_for(root / "finished.txt")


@pytest.mark.parametrize("option", ["--html", "--json"])
def test_batch_cli_retains_output_and_exit_code(isolated_launcher, option):
    root = isolated_launcher
    completed = run_batch(root, ["данные.csv", option, "отчет с пробелами.html"])
    assert completed.returncode == 7
    assert b"CLI completed" in completed.stdout
    recorded = json.loads((root / "started.json").read_text(encoding="utf-8"))
    assert Path(recorded["executable"]).name.lower() == "python.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch integration")
def test_real_batch_exports_reports_and_returns_application_errors(tmp_path):
    dataset = tmp_path / "данные с пробелами.csv"
    dataset.write_text("name,value\na,1\nb,2\n", encoding="utf-8")
    html = tmp_path / "отчет.html"
    report = tmp_path / "отчет.json"
    completed = run_batch(ROOT, [str(dataset), "--html", str(html), "--json", str(report)])
    assert completed.returncode == 0, completed.stderr
    assert html.exists() and json.loads(report.read_text(encoding="utf-8"))["rows"] == 2
    failed = run_batch(ROOT, [str(tmp_path / "missing.csv"), "--html", str(html)])
    assert failed.returncode == 1 and failed.stderr
    invalid = run_batch(ROOT, ["--json", str(report)])
    assert invalid.returncode == 2 and invalid.stderr


def test_gui_startup_failure_has_log_and_visible_message(tmp_path, monkeypatch):
    from data_xray import __main__ as application
    messages = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(gui_entry, "show_failure", messages.append)

    def fail(argv):
        raise ImportError("Qt startup failure")

    monkeypatch.setattr(application, "main", fail)
    assert gui_entry.main([]) == 1
    log_path = tmp_path / "DataXRay" / "logs" / "startup.log"
    assert "Qt startup failure" in log_path.read_text(encoding="utf-8")
    assert len(messages) == 1 and str(log_path) in messages[0]


def test_missing_pythonw_reports_launch_error(monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    messages = []
    monkeypatch.setattr(gui_entry, "show_failure", messages.append)

    def fail(*args, **kwargs):
        raise FileNotFoundError("pythonw.exe missing")

    monkeypatch.setattr(launcher.subprocess, "Popen", fail)
    assert launcher.main([]) == 1
    assert "pythonw.exe missing" in capsys.readouterr().err
    assert len(messages) == 1 and "pythonw.exe missing" in messages[0]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop integration")
def test_real_batch_starts_real_gui_with_isolated_settings(tmp_path, monkeypatch):
    # Observe the real entry chain without replacing launcher, gui_entry or UI.
    # sitecustomize runs only in these subprocesses; every setting stays in INI.
    observer = tmp_path / "observer"
    observer.mkdir()
    (observer / "sitecustomize.py").write_text(r'''
import json
import os
from pathlib import Path
import sys
import traceback

if "gui_entry" in " ".join(sys.orig_argv):
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from data_xray import ui
    directory = Path(os.environ["DATA_XRAY_ACCEPTANCE"])
    settings = QSettings(str(directory / "settings.ini"), QSettings.Format.IniFormat)
    ui.QSettings = lambda *args, **kwargs: settings
    settings.setValue("help/show_on_startup", False)
    settings.sync()
    original = ui.run_gui
    def observed(*args, **kwargs):
        import ctypes
        app = QApplication([])
        timer = QTimer()
        def inspect():
            try:
                windows = [w for w in app.topLevelWidgets() if isinstance(w, ui.MainWindow)]
                if not windows:
                    return
                window = windows[0]
                if window.worker.isRunning() or not window.analyze_button.isEnabled():
                    return
                assert window.isVisible() and window.result is not None
                assert (window.result.rows, window.result.columns, len(window.result.findings)) == (121, 8, 17)
                assert [w for w in app.topLevelWidgets() if w.isVisible()] == [window]
                (directory / "gui-started.json").write_text(json.dumps({
                    "console": bool(ctypes.windll.kernel32.GetConsoleWindow()),
                    "executable": sys.executable, "settings": window.settings.fileName(),
                    "source": window.result.source,
                }), encoding="utf-8")
                if (directory / "release.txt").exists():
                    timer.stop()
                    window.close()
                    app.quit()
            except BaseException:
                (directory / "gui-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                timer.stop()
                app.exit(1)
        timer.timeout.connect(inspect)
        timer.start(20)
        QTimer.singleShot(12000, app.quit)
        code = original(*args, **kwargs)
        (directory / "gui-finished.txt").write_text(str(code), encoding="utf-8")
        return code
    ui.run_gui = observed
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(observer), str(ROOT))))
    monkeypatch.setenv("DATA_XRAY_ACCEPTANCE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    try:
        completed = run_batch(ROOT, [str(ROOT / "examples" / "demo.csv")])
        assert completed.returncode == 0, completed.stderr
        wait_for(tmp_path / "gui-started.json")
        record = json.loads((tmp_path / "gui-started.json").read_text(encoding="utf-8"))
        assert record["console"] is False
        assert Path(record["executable"]).name.lower() == "pythonw.exe"
        assert Path(record["settings"]).is_relative_to(tmp_path)
        assert Path(record["source"]) == ROOT / "examples" / "demo.csv"
        assert not (tmp_path / "gui-finished.txt").exists()
    finally:
        (tmp_path / "release.txt").write_text("release", encoding="utf-8")
    wait_for(tmp_path / "gui-finished.txt")
    assert (tmp_path / "gui-finished.txt").read_text(encoding="utf-8") == "0"
    assert not (tmp_path / "gui-error.txt").exists()
    log = tmp_path / "DataXRay" / "logs" / "startup.log"
    assert log.exists() and "Traceback" not in log.read_text(encoding="utf-8")
