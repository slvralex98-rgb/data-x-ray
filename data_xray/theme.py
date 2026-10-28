"""Local, consistent Qt theme. SVG controls ship with the application."""
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget


def _icon(name: str) -> str:
    return (Path(__file__).parent / "assets" / f"{name}.svg").as_posix()


DOCUMENT_STYLE = """
body{font:14px 'Segoe UI';color:#e8edf4;background:#181e27}
h2{font-size:22px;color:#fff3e6;margin-top:12px}
th,td{padding:8px;border-bottom:1px solid #34404e}
th{background:#252e3b;color:#f4e0cd}
p{line-height:1.6}.muted{color:#a6b1c2}.eyebrow{color:#ffb776}
a{color:#ffb776}
"""

# List-style combo popups measure the styled rows; menu-style popups clipped
# the final item when view padding and row padding were combined.
STYLE = """
QWidget{background:#10141b;color:#e8edf4;font-family:"Segoe UI";font-size:13px}
QLabel{background:transparent}QLabel#brand{font-size:25px;font-weight:700;color:#fff2e7}
QLabel#title{font-size:29px;font-weight:700;color:#f3f5f9}
QLabel#muted{color:#a6b1c2}QLabel#section{font-weight:700;font-size:15px}
QLabel#metric{font-size:30px;font-weight:700;color:#ffb06d}
QFrame#sidebar{background:#171d26;border:1px solid #df8548;border-radius:16px}
QFrame#sidebar QPushButton{padding:8px 10px}
QWidget#analysisPanel{background:transparent}
QWidget#analysisPanel QSpinBox,QWidget#analysisPanel QComboBox{padding:6px;padding-right:32px}
QFrame#sidebar QLabel{color:#c6cfdb}QFrame#sidebar QLabel#brand{color:#fff2e7}
QFrame#card{background:#1b222d;border:1px solid #df8548;border-radius:12px}
QPushButton{background:#242d3a;color:#e8edf4;border:1px solid #536073;border-radius:8px;padding:10px 14px;font-weight:600}
QPushButton:hover{background:#313b4a;border-color:#ffad68;color:#fff4e9}
QPushButton:pressed{background:#3c3028;border-color:#ffad68}
QPushButton:focus{border:1px solid #ffad68}
QPushButton#primary{background:#f28c45;color:#17130f;border:1px solid #ffb578}
QPushButton#primary:hover{background:#ffab69;border-color:#ffd2ac}
QPushButton#primary:pressed{background:#d57532}
QPushButton:disabled,QPushButton#primary:disabled{background:#202731;color:#788596;border-color:#384352}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:#10161f;color:#e8edf4;border:1px solid #536073;border-radius:7px;padding:8px;selection-background-color:#714525;selection-color:#fff3e8}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus{border-color:#ffad68}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled{background:#202731;color:#788596;border-color:#384352}
QLineEdit{placeholder-text-color:#8c99ad}
QComboBox{padding-right:32px;min-height:18px;combobox-popup:0}
QComboBox::drop-down{width:29px;border:0;border-left:1px solid #3d495a;border-top-right-radius:7px;border-bottom-right-radius:7px;background:#283241}
QComboBox::drop-down:hover{background:#3c332d}
QComboBox::down-arrow{image:url("@down@");width:12px;height:8px}
QComboBox::down-arrow:disabled{image:url("@down-muted@");width:12px;height:8px}
QComboBox QAbstractItemView{background:#202936;color:#e8edf4;border:1px solid #f28c45;outline:0;padding:0;selection-background-color:#704322;selection-color:#fff5eb}
QComboBox QAbstractItemView::item{min-height:30px;padding:5px 8px;border-radius:3px}
QComboBox QAbstractItemView::item:selected{background:#704322;color:#fff5eb}
QSpinBox,QDoubleSpinBox{padding-right:32px;min-height:18px}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:28px;background:#283241;border:0;border-left:1px solid #3d495a}
QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;border-top-right-radius:7px;border-bottom:1px solid #3d495a}
QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;border-bottom-right-radius:7px}
QSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{background:#51402f}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{image:url("@up@");width:10px;height:6px}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{image:url("@down@");width:10px;height:6px}
QSpinBox::up-arrow:disabled,QDoubleSpinBox::up-arrow:disabled{image:url("@up-muted@");width:10px;height:6px}
QSpinBox::down-arrow:disabled,QDoubleSpinBox::down-arrow:disabled{image:url("@down-muted@");width:10px;height:6px}
QFrame#tabsFrame{background:#181e27;border:1px solid #df8548;border-radius:12px}
QFrame#tableFrame{background:#181e27;border:1px solid #df8548;border-radius:8px}
QTabWidget::pane{background:#181e27;border:0}
QTabBar::tab{background:#181e27;padding:12px 17px;margin-right:4px;color:#a6b1c2;border-bottom:3px solid transparent;font-weight:400}
QTabBar::tab:hover{color:#ffd0a7;background:#1d242e}
QTabBar::tab:selected{color:#ffb776;border-bottom:3px solid #ff9b50;font-weight:400}
QListWidget,QTextBrowser,QTableView{background:#181e27;color:#e8edf4;border:1px solid #3c4859;border-radius:7px;selection-background-color:#654126;selection-color:#fff3e6;outline:0}
QListWidget::item{padding:15px 12px;border-bottom:1px solid #35404f;border-left:3px solid transparent}
QListWidget::item:hover{background:#252f3c}
QListWidget::item:selected{background:#392e26;border-left:3px solid #ff9b50;color:#ffdab9}
QHeaderView{background:#252e3b}
QHeaderView::section{background:#252e3b;color:#cbd5e2;padding:10px;border:0;border-right:1px solid #3b4758;border-bottom:1px solid #536073;font-weight:600}
QTableView{border:0;border-radius:0;gridline-color:#34404e;alternate-background-color:#1e2632}
QTableView::item{padding:4px}
QTableCornerButton::section{background:#252e3b;border:0;border-right:1px solid #3b4758;border-bottom:1px solid #536073}
QScrollBar:vertical{background:#10161f;width:14px;margin:2px;border-radius:6px}
QScrollBar:horizontal{background:#10161f;height:14px;margin:2px;border-radius:6px}
QScrollBar::handle:vertical{background:#6a788d;min-height:30px;border:3px solid #10161f;border-radius:6px}
QScrollBar::handle:horizontal{background:#6a788d;min-width:30px;border:3px solid #10161f;border-radius:6px}
QScrollBar::handle:hover{background:#ffad68}
QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;border:0;background:transparent}
QScrollBar::add-page,QScrollBar::sub-page{background:transparent}
QAbstractScrollArea::corner{background:#10161f;border:0}
QProgressBar{border:0;background:#2d3745;border-radius:4px;height:7px;max-height:7px}
QProgressBar::chunk{background:#f28c45;border-radius:4px}
QSplitter::handle{background:#181e27;width:8px;height:8px}
QSplitter::handle:hover{background:#b96230}
QStatusBar{color:#a6b1c2;background:#10141b}QStatusBar::item{border:0}
QDialog,QMessageBox,QFileDialog{background:#171d26;color:#e8edf4}
QDialog{border:1px solid #df8548;border-radius:10px}
QToolTip{background:#2b3543;color:#fff0df;border:1px solid #f28c45;padding:7px}
QCheckBox{background:transparent;spacing:9px;color:#e8edf4}
QCheckBox::indicator{width:18px;height:18px;border:1px solid #718096;border-radius:4px;background:#10161f}
QCheckBox::indicator:hover{border-color:#ffad68}
QCheckBox::indicator:checked{background:#f28c45;border-color:#ffb578;image:url("@check@")}
QCheckBox:disabled{color:#788596}
QDialogButtonBox{background:transparent}
QMenu{background:#202936;color:#e8edf4;border:1px solid #b96230;padding:5px}
QMenu::item{padding:7px 20px}QMenu::item:selected{background:#704322;color:#fff5eb}
"""
for _name in ("down", "up", "down-muted", "up-muted", "check"):
    STYLE = STYLE.replace(f"@{_name}@", _icon(_name))


def neon_panel(widget: QWidget) -> None:
    """A restrained outer halo keeps the content itself crisp."""
    glow = QGraphicsDropShadowEffect(widget)
    glow.setBlurRadius(18)
    glow.setOffset(0, 0)
    glow.setColor(QColor(242, 140, 69, 48))
    widget.setGraphicsEffect(glow)


def apply_theme(app: QApplication) -> None:
    """Also style detached combo popups and application-owned dialogs."""
    if app.property("dataXRayTheme"):
        return
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#171d26"),
        (QPalette.ColorRole.WindowText, "#e8edf4"),
        (QPalette.ColorRole.Base, "#181e27"),
        (QPalette.ColorRole.AlternateBase, "#1e2632"),
        (QPalette.ColorRole.Text, "#e8edf4"),
        (QPalette.ColorRole.Button, "#242d3a"),
        (QPalette.ColorRole.ButtonText, "#e8edf4"),
        (QPalette.ColorRole.Highlight, "#714525"),
        (QPalette.ColorRole.HighlightedText, "#fff3e8"),
        (QPalette.ColorRole.ToolTipBase, "#2b3543"),
        (QPalette.ColorRole.ToolTipText, "#fff0df"),
        (QPalette.ColorRole.Link, "#ffb776"),
        (QPalette.ColorRole.PlaceholderText, "#8c99ad"),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#788596"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    app.setProperty("dataXRayTheme", True)
