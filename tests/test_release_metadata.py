"""Release identity stays consistent without loading the GUI into core imports."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data_xray import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_documentation_names_current_version():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert __version__ in readme.splitlines()[0]
    assert f"## {__version__}" in changelog.splitlines()


def test_package_import_exposes_version_without_loading_qt():
    script = '''
import sys
import data_xray
assert data_xray.__version__ == sys.argv[1]
assert not any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules)
assert "data_xray.ui" not in sys.modules
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, __version__], cwd=ROOT,
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_build_metadata_resolves_the_package_version():
    tomllib = pytest.importorskip("tomllib", reason="TOML parser is standard in Python 3.11+")
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in configuration["project"]
    assert "version" in configuration["project"]["dynamic"]
    attribute = configuration["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    module, name = attribute.rsplit(".", 1)
    import importlib
    assert getattr(importlib.import_module(module), name) == __version__


@pytest.mark.parametrize("size", [(1000, 680), (1400, 900)])
def test_version_remains_below_help_inside_sidebar(tmp_path, size):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QSettings
    from PySide6.QtWidgets import QApplication
    from data_xray.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    try:
        window.resize(*size)
        window.show()
        app.processEvents()
        label = window.version_label
        sidebar = label.parentWidget()
        assert label.text() == f"Версия {__version__}"
        assert label.isVisible()
        assert sidebar.objectName() == "sidebar"
        assert sidebar.rect().contains(label.geometry())
        assert label.geometry().top() > window.help_button.geometry().bottom()
        assert window.centralWidget().rect().contains(
            label.mapTo(window.centralWidget(), QPoint(0, label.height() - 1))
        )
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
        window.statusBar().showMessage("Проверка выполняется…")
        app.processEvents()
        assert label.text() == f"Версия {__version__}" and label.isVisible()
    finally:
        window.close()
        app.processEvents()
