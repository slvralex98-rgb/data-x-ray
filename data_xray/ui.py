from __future__ import annotations

from pathlib import Path
from threading import Event
from dataclasses import replace

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal, Slot, QSettings, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget, QTableView,
    QTextBrowser, QVBoxLayout, QWidget, QLineEdit, QAbstractItemView, QDialog, QSizePolicy,
)

from . import __version__
from .checks import AnalysisCancelled
from .demo import create_demo
from .dialogs import ImportDialog, TourDialog, HelpPrompt, separator_combo, encoding_combo, select_value
from .engine import Analyzer
from .loaders import load_dataset
from .models import AnalysisOptions
from .overlays import LoadingOverlay
from .report import SEVERITY, TYPES, e, finding_html, export_html
from .theme import DOCUMENT_STYLE, STYLE, apply_theme, neon_panel
from .rule_ui import RulePanel, RuleWorker
from .criteria import evaluations_to_findings
from .criteria_ui import CriteriaPanel
from .passport_ui import PassportPanel
from .finding_review_ui import FindingReviewBar


class DataModel(QAbstractTableModel):
    """Virtual view: Qt asks for visible cells instead of creating one widget per cell."""
    def __init__(self, headers, rows, parent=None):
        super().__init__(parent)
        self.headers, self.rows = headers, rows

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def replace(self, headers, rows):
        self.beginResetModel()
        self.headers, self.rows = headers, rows
        self.endResetModel()

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return "∅ NULL" if value is None else str(value)[:600]
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(value)[:4000]
        if role == Qt.ItemDataRole.ForegroundRole and value is None:
            return QColor("#ffa8b1")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)
        return None


class AnalysisWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path, load_options, analysis_options, parent=None):
        super().__init__(parent)
        self.path, self.load_options, self.options = path, load_options, analysis_options
        self.cancel_event = Event()

    def run(self):
        try:
            self.progress.emit(1, "Чтение файла…")
            dataset = load_dataset(self.path, max_rows=self.options.max_rows, **self.load_options)
            result = Analyzer().analyze(dataset, self.options, self.progress.emit, self.cancel_event)
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(dataset, result)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")




class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.setWindowTitle("Data X-Ray")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 680)
        self.dataset = self.result = self.worker = None
        self.path = None
        self._loaded_signature = None
        self._busy = False
        self._mine_after_analysis = False
        self._check_after_analysis = False
        self.settings = settings if settings is not None else QSettings("DataXRay", "Data X-Ray")
        self.tour = self.help_prompt = None
        self.visible_findings = []
        apply_theme(QApplication.instance())
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(22)
        layout.addWidget(self._sidebar())
        main = QVBoxLayout()
        main.setSpacing(16)
        layout.addLayout(main, 1)
        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Анализ данных")
        title.setObjectName("title")
        title.setWordWrap(True)
        titles.addWidget(title)
        title_row.addLayout(titles, 1)
        self.export_button = QPushButton("Экспорт HTML")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        title_row.addWidget(self.export_button)
        main.addLayout(title_row)
        self.source_label = QLabel("Откройте CSV, XLSX или Parquet — либо начните с демо.")
        self.source_label.setWordWrap(True)
        self.source_label.setObjectName("muted")
        main.addWidget(self.source_label)
        cards = QHBoxLayout()
        self.metrics = []
        for caption in ("ЗАПИСИ", "СТОЛБЦЫ", "ПРОПУСКИ", "РЕЗУЛЬТАТЫ"):
            card = QFrame()
            card.setObjectName("card")
            neon_panel(card)
            col = QVBoxLayout(card)
            col.setContentsMargins(18, 13, 18, 13)
            name = QLabel(caption)
            name.setObjectName("muted")
            col.addWidget(name)
            value = QLabel("—")
            value.setObjectName("metric")
            col.addWidget(value)
            self.metrics.append(value)
            cards.addWidget(card)
        main.addLayout(cards)
        self.tabs = QTabWidget()
        self.tabs_frame = self._content_frame(self.tabs, "tabsFrame", 10)
        main.addWidget(self.tabs_frame, 1)
        self._findings_tab()
        self.profile_view = self._table()
        self.tabs.addTab(self._content_frame(self.profile_view, "tableFrame", 6), "Профиль столбцов")
        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        self.preview_label = QLabel("Первые 1 000 записей")
        self.preview_label.setObjectName("muted")
        preview_layout.addWidget(self.preview_label)
        self.reset_preview_button = QPushButton("Первые записи датасета")
        self.reset_preview_button.setEnabled(False)
        self.reset_preview_button.clicked.connect(self.reset_preview)
        preview_layout.addWidget(self.reset_preview_button)
        self.preview_view = self._table()
        preview_layout.addWidget(self._content_frame(self.preview_view, "tableFrame", 6))
        self.tabs.addTab(preview_tab, "Данные")
        self.method_view = QTextBrowser()
        self.method_view.document().setDefaultStyleSheet(DOCUMENT_STYLE)
        self.tabs.addTab(self.method_view, "Методика")
        self.rule_panel = RulePanel(self)
        self.tabs.addTab(self.rule_panel, "Поиск правил")
        self.passport_panel = PassportPanel(self)
        self.tabs.addTab(self.passport_panel, "Паспорт таблицы")
        self.criteria_panel = CriteriaPanel(self)
        self.tabs.addTab(self.criteria_panel, "Мои проверки")
        self.criteria_panel.changed.connect(self._custom_criteria_changed)
        # All sections remain reachable when the tab strip needs to scroll.
        self.section_picker.addItems([self.tabs.tabText(i) for i in range(self.tabs.count())])
        self.section_picker.currentIndexChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.section_picker.setCurrentIndex)
        for control in (self.separator, self.encoding, self.sheet):
            control.currentIndexChanged.connect(self._rule_source_changed)
        self.row_limit.valueChanged.connect(self._rule_source_changed)
        # Retain the value/signal API without reserving a strip below the data.
        self.progress = QProgressBar(self)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.loading_overlay = LoadingOverlay(root)
        self.statusBar().showMessage("Готов к работе")
        self.detail.setHtml("<h2>Начните с одного файла</h2><p>Здесь появятся проблемы и подсказки по приоритету: что обнаружено, почему и какие записи проверить. Среди подсказок — кандидаты в ключи и постоянные значения; они не обязательно указывают на ошибку.</p><p>Демо содержит известные дефекты: пропуски, дубликат, неверные числа и даты, выброс и строковые аномалии.</p>")

    def _sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        neon_panel(sidebar)
        sidebar.setFixedWidth(246)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 20, 20, 18)
        side.setSpacing(8)
        brand = QLabel("Data X-Ray")
        brand.setObjectName("brand")
        side.addWidget(brand)
        self.open_button = QPushButton("Открыть датасет…")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.open_file)
        side.addWidget(self.open_button)
        self.demo_button = QPushButton("Попробовать демо")
        self.demo_button.clicked.connect(self.open_demo)
        side.addWidget(self.demo_button)
        self.import_button = QPushButton("Настройки импорта…")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.edit_import)
        side.addWidget(self.import_button)
        side.addWidget(QLabel("Раздел"))
        self.section_picker = QComboBox()
        self.section_picker.setToolTip("Перейти к разделу анализа")
        side.addWidget(self.section_picker)
        # Preserve the programmatic/CLI API; drafts live in ImportDialog.
        self.separator = separator_combo(self)
        self.encoding = encoding_combo(self)
        self.sheet = QComboBox(self)
        self.sheet.addItem("Первый лист", None)
        for control in (self.separator, self.encoding, self.sheet):
            control.hide()
        self.analysis_panel = QWidget()
        self.analysis_panel.setObjectName("analysisPanel")
        self.analysis_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        parameters = QVBoxLayout(self.analysis_panel)
        parameters.setContentsMargins(0, 4, 0, 4)
        parameters.setSpacing(6)
        parameters.addWidget(QLabel("ПАРАМЕТРЫ АНАЛИЗА"))
        parameters.addWidget(QLabel("Лимит записей"))
        self.row_limit = QSpinBox()
        self.row_limit.setRange(1, 2_000_000)
        self.row_limit.setSingleStep(10000)
        self.row_limit.setValue(200000)
        self.row_limit.setGroupSeparatorShown(True)
        self.row_limit.lineEdit().setTextMargins(10, 0, 8, 0)
        self.row_limit.setToolTip("Читать первые записи файла, с учетом ограничения 5 млн ячеек.")
        parameters.addWidget(self.row_limit)
        parameters.addWidget(QLabel("Примеров на результат"))
        self.example_rows = QSpinBox()
        self.example_rows.setRange(1, 50)
        self.example_rows.setValue(8)
        self.example_rows.lineEdit().setTextMargins(10, 0, 8, 0)
        self.example_rows.setToolTip("Число примеров в пояснениях и HTML-отчете; не влияет на число обнаруженных записей.")
        parameters.addWidget(self.example_rows)
        side.addWidget(self.analysis_panel)
        self.analyze_button = QPushButton("Анализировать")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.start_analysis)
        side.addWidget(self.analyze_button)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        side.addWidget(self.cancel_button)
        side.addStretch()
        self.help_button = QPushButton("Как пользоваться")
        self.help_button.clicked.connect(self.show_tour)
        side.addWidget(self.help_button)
        self.version_label = QLabel(f"Версия {__version__}")
        self.version_label.setObjectName("muted")
        side.addWidget(self.version_label)
        return sidebar

    @staticmethod
    def _content_frame(content, name, inset):
        """Own the border outside child painting, headers and scrollbars."""
        frame = QFrame()
        frame.setObjectName(name)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(inset, inset, inset, inset)
        layout.setSpacing(0)
        layout.addWidget(content)
        return frame

    def _findings_tab(self):
        page = QWidget()
        col = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по проблеме, подсказке или столбцу…")
        self.search.textChanged.connect(self.filter_findings)
        filters.addWidget(self.search, 1)
        self.severity = QComboBox()
        self.severity.addItem("Все приоритеты", None)
        for key, name in SEVERITY.items():
            self.severity.addItem(name, key)
        self.severity.currentIndexChanged.connect(self.filter_findings)
        filters.addWidget(self.severity)
        col.addLayout(filters)
        self.finding_review = FindingReviewBar(self)
        col.addWidget(self.finding_review)
        self.findings_count = QLabel("Проблем и подсказок пока нет")
        self.findings_count.setObjectName("muted")
        col.addWidget(self.findings_count)
        split = QSplitter()
        self.findings_list = QListWidget()
        self.findings_list.setWordWrap(True)
        self.findings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.findings_list.currentRowChanged.connect(self.show_finding)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.document().setDefaultStyleSheet(DOCUMENT_STYLE)
        split.addWidget(self.findings_list)
        split.addWidget(self.detail)
        split.setSizes([360, 640])
        col.addWidget(split, 1)
        self.affected_button = QPushButton("Показать затронутые записи →")
        self.affected_button.setEnabled(False)
        self.affected_button.clicked.connect(self.show_affected)
        col.addWidget(self.affected_button)
        self.tabs.addTab(page, "Проблемы и подсказки")

    @staticmethod
    def _table():
        table = QTableView()
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setDefaultSectionSize(155)
        table.verticalHeader().setDefaultSectionSize(34)
        return table

    def load_options(self):
        return {"separator": self.separator.currentData(), "encoding": self.encoding.currentData(), "sheet": self.sheet.currentData()}

    def _apply_load_options(self, options):
        for widget, key in ((self.separator, "separator"), (self.encoding, "encoding"), (self.sheet, "sheet")):
            select_value(widget, options[key])

    @Slot()
    def open_file(self):
        if self.worker and self.worker.isRunning():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Открыть датасет", "", "Датасеты (*.csv *.xlsx *.parquet)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self._configure_import(path, existing=False)

    def _configure_import(self, path, *, existing):
        try:
            dialog = ImportDialog(path, self.load_options() if existing else None, self)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось прочитать книгу", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.select_path(path)
            self._apply_load_options(dialog.options())
            self.source_label.setText(f"Выбран файл: {path}. Настройки применены — нажмите «Анализировать». Предыдущий результат сохранен.")

    @Slot()
    def edit_import(self):
        if self.path and not (self.worker and self.worker.isRunning()):
            self._configure_import(self.path, existing=True)

    def select_path(self, path, auto_start=False):
        """Non-interactive path for CLI, demos and automation; never opens a dialog."""
        if self.worker and self.worker.isRunning():
            return
        self.rule_panel.invalidate()
        self.criteria_panel.invalidate()
        self.passport_panel.invalidate()
        self._loaded_signature = None
        self._clear_custom_findings()
        self.path = str(path)
        self.sheet.clear()
        self.sheet.addItem("Первый лист", None)
        self.source_label.setText(f"Выбран файл: {path}. Нажмите «Анализировать».")
        self.source_label.setToolTip(str(path))
        self.analyze_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.rule_panel.update_buttons()
        self.criteria_panel.update_buttons()
        self.passport_panel.update_buttons()
        self.finding_review.update_buttons()
        if auto_start:
            self.start_analysis()

    @Slot()
    def show_tour(self):
        if self.help_prompt is not None and self.help_prompt.isVisible():
            self.help_prompt.reject()
        if self.tour is not None and self.tour.isVisible():
            self.tour.raise_()
            self.tour.setFocus()
            return
        if self.tour is not None:
            self.tour.highlight.deleteLater()
            self.tour.pointer.deleteLater()
            self.tour.escape.deleteLater()
            self.tour.deleteLater()
        self.tour = TourDialog(self, self.settings)
        self.tour.show()

    def show_first_run_help(self):
        """Explicit startup hook; constructing MainWindow never opens popups."""
        if not self.settings.value("help/show_on_startup", True, type=bool):
            return
        if self.help_prompt is not None and self.help_prompt.isVisible():
            return
        self.help_prompt = HelpPrompt(self.settings, self)
        self.help_prompt.finished.connect(lambda result: self.show_tour() if result == QDialog.DialogCode.Accepted else None)
        self.help_prompt.show()

    @Slot()
    def open_demo(self):
        # User data directory keeps installed packages read-only and avoids overwrites.
        from PySide6.QtCore import QStandardPaths
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "demo"
        try:
            path = create_demo(folder, all_formats=False)
            self._apply_load_options({"separator": None, "encoding": "utf-8-sig", "sheet": None})
            self.select_path(path, auto_start=True)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось создать демо", str(exc))

    @Slot()
    def start_analysis(self):
        if not self.path or self.worker and self.worker.isRunning():
            return
        self.worker = AnalysisWorker(self.path,
            self.load_options(),
            AnalysisOptions(max_rows=self.row_limit.value(), example_rows=self.example_rows.value()), self)
        self._check_after_analysis = bool(self.criteria_panel.rules)
        self._connect_worker(self.worker, self.on_completed)
        self._running_signature = self._source_signature()
        self._begin_worker()

    def _connect_worker(self, worker, completed):
        # Queued signals from a preceding thread must never settle a newer run.
        for signal, callback in ((worker.progress, self.on_progress), (worker.completed, completed),
                                 (worker.failed, self.on_failed), (worker.cancelled, self.on_cancelled),
                                 (worker.finished, self.on_finished)):
            signal.connect(lambda *args, w=worker, cb=callback: cb(*args) if self.worker is w else None)

    def _begin_worker(self):
        self._set_busy(True)
        self.progress.setValue(0)
        self.loading_overlay.begin(self.tabs_frame)
        for card in (self.tour, self.help_prompt):
            if card and card.isVisible():
                card.reposition()
        self.worker.start()

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.open_button, self.demo_button, self.separator, self.encoding, self.sheet, self.row_limit, self.example_rows):
            widget.setEnabled(not busy)
        self.import_button.setEnabled(not busy and self.path is not None)
        self.analyze_button.setEnabled(not busy and self.path is not None)
        self.cancel_button.setEnabled(busy)
        self.export_button.setEnabled(not busy and self.result is not None)
        self.rule_panel.update_buttons()
        self.criteria_panel.update_buttons()
        self.passport_panel.update_buttons()
        self.finding_review.update_buttons()
        row = self.findings_list.currentRow()
        self.affected_button.setEnabled(not busy and self.rule_source_ready() and 0 <= row < len(self.visible_findings))

    @Slot(int, str)
    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.loading_overlay.set_stage(value, message)
        self.statusBar().showMessage(message)

    @Slot(object, object)
    def on_completed(self, dataset, result):
        # All models/cards are committed in a single paint transaction beneath
        # the opaque loader; no empty intermediate frame reaches the screen.
        self.centralWidget().setUpdatesEnabled(False)
        try:
            self._apply_result(dataset, result)
        finally:
            self.centralWidget().setUpdatesEnabled(True)
        self.loading_overlay.title.setText("Анализ завершен")
        self.loading_overlay.stage.setText(f"Найдено результатов: {len(result.findings)}")
        self.loading_overlay.hint.setText("Готовим просмотр")

    def _apply_result(self, dataset, result):
        self.rule_panel.invalidate()
        self.criteria_panel.invalidate()
        self.dataset, self.result = dataset, result
        self._loaded_signature = getattr(self, "_running_signature", self._source_signature())
        self.finding_review.set_result(result)
        try:
            self.passport_panel.set_current(dataset, result)
        except ValueError as exc:
            self.passport_panel.invalidate()
            self.passport_panel.detail.setHtml("<h2>Паспорт недоступен</h2><p>" + e(exc, 2000) + "</p>")
        values = (result.rows, result.columns, sum(p.missing for p in result.profiles), len(result.findings))
        for label, value in zip(self.metrics, values):
            label.setText(f"{value:,}".replace(",", " "))
        self.source_label.setText(f"{Path(result.source).name} · {result.source_format.upper()} · {'ТОЛЬКО ПЕРВЫЕ ' + str(result.rows) + ' ЗАПИСЕЙ' if result.truncated else 'Весь датасет'} · {result.elapsed_seconds:.2f} сек.")
        self.source_label.setToolTip(result.source)
        self.filter_findings()
        headers = ["Столбец", "Физический тип", "Похож на", "Пропуски", "NULL", "Пустые", "Маркеры", "Различные", "Уникальность", "Статистика", "Частые значения"]
        rows = [[p.name, p.physical_type, TYPES[p.inferred_type], f"{p.missing} ({p.missing / max(1, result.rows):.1%})", p.nulls, p.blanks, p.markers, p.distinct, f"{p.unique_ratio:.1%}", p.statistics, p.top_values] for p in result.profiles]
        self._replace_table(self.profile_view, headers, rows)
        self.set_preview(list(range(min(1000, result.rows))), "Первые записи датасета")
        self.reset_preview_button.setEnabled(True)
        self.method_view.setHtml(f"<h2>Как читать результат</h2><p>Наблюдение — факт в данных. Гипотеза — эвристика, которую нужно проверить. Никакие значения автоматически не исправляются.</p><ul>" + "".join("<li>" + e(n, 4000) + "</li>" for n in result.notes) + f"</ul><p>Приоритет: высокий 80, средний 55, низкий 30, информация 5; плюс до 15 за долю записей и 5 за наблюдение. Выбросы: Q1/Q3 ± {result.options.iqr_multiplier:g} × IQR; при IQR=0 редкие отклонения отмечаются отдельно. Пропуски: NULL, пустые строки, null / none / n/a / na / nan. Кандидаты в ключи проверяются по исходным значениям. В таблице «Данные» показано до 1 000 записей; отчет содержит до {result.options.example_rows} примеров на результат проверки. Поддерживаются скалярные поля; даты с / трактуются как день/месяц/год.</p>")
        self.statusBar().showMessage(f"Анализ завершен · результатов: {len(result.findings)}")

    @Slot()
    def on_finished(self):
        self._set_busy(False)
        self.loading_overlay.finish()
        if self._check_after_analysis:
            self._check_after_analysis = False
            if self.rule_source_ready() and self.criteria_panel.rules:
                QTimer.singleShot(0, self.start_custom_checks)
                return
        if self._mine_after_analysis:
            self._mine_after_analysis = False
            if self.rule_source_ready():
                QTimer.singleShot(0, self.start_mining)

    @Slot(str)
    def on_failed(self, message):
        self._mine_after_analysis = False
        self._check_after_analysis = False
        self.loading_overlay.title.setText("Анализ не выполнен")
        self.loading_overlay.stage.setText("Предыдущий результат сохранен")
        self.loading_overlay.hint.setText("Проверьте файл и настройки импорта")
        self.statusBar().showMessage("Анализ не выполнен. Предыдущий результат сохранен.")
        QMessageBox.warning(self, "Не удалось выполнить анализ", message + "\n\nДля CSV проверьте кодировку, разделитель и заголовки.")

    @Slot()
    def on_cancelled(self):
        self._mine_after_analysis = False
        self._check_after_analysis = False
        self.loading_overlay.title.setText("Анализ отменен")
        self.loading_overlay.stage.setText("Предыдущий результат сохранен")
        self.loading_overlay.hint.setText("Можно запустить анализ снова")
        self.statusBar().showMessage("Анализ отменен. Предыдущий результат сохранен.")
        self.progress.setValue(0)

    @Slot()
    def cancel_analysis(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.statusBar().showMessage("Отмена после текущего этапа чтения или проверки…")
            self.loading_overlay.stage.setText("Отмена после текущего этапа…")

    @Slot()
    def filter_findings(self):
        if not self.result:
            return
        query = self.search.text().casefold().strip()
        severity = self.severity.currentData()
        selected = self.visible_findings[self.findings_list.currentRow()] if 0 <= self.findings_list.currentRow() < len(self.visible_findings) else None
        self.visible_findings = self.finding_review.filter([f for f in self.result.findings if (not severity or f.severity == severity) and (not query or query in (f.title + " " + " ".join(f.columns)).casefold())])
        self.findings_list.blockSignals(True)
        self.findings_list.clear()
        colors = {"high": "#ffabb3", "medium": "#ffd08a", "low": "#a4c8ff", "info": "#c4b9f5"}
        for f in self.visible_findings:
            item = QListWidgetItem(f"{self.finding_review.badge(f)} · {SEVERITY[f.severity]} · {f.score} / записей: {f.count:,}\n{f.title}\n{' · '.join(f.columns)}")
            item.setForeground(QColor(colors[f.severity]))
            item.setToolTip(f.title + " · " + ", ".join(f.columns))
            self.findings_list.addItem(item)
        self.findings_count.setText(f"Показано {len(self.visible_findings)} из {len(self.result.findings)} · по убыванию приоритета")
        self.findings_list.blockSignals(False)
        if self.visible_findings:
            self.findings_list.setCurrentRow(next((i for i, f in enumerate(self.visible_findings) if f is selected), 0))
        else:
            self.detail.setHtml("<h2>Нет результатов по этому фильтру</h2><p>Измените фильтр или поиск. Отсутствие проблем и подсказок не подтверждает отсутствие бизнес-ошибок.</p>")
            self.affected_button.setEnabled(False)
        self.finding_review.selection_changed()

    @Slot(int)
    def show_finding(self, row):
        if 0 <= row < len(self.visible_findings):
            finding = self.visible_findings[row]
            self.detail.setHtml(self.finding_review.detail_html(finding) + finding_html(finding))
            self.affected_button.setEnabled(not self._busy and self.rule_source_ready())
        self.finding_review.selection_changed()

    def set_preview(self, indices, title):
        headers = ["Запись №"] + self.dataset.frame.columns
        rows = [[i + 1] + list(self.dataset.frame.row(i)) for i in indices[:1000]]
        self._replace_table(self.preview_view, headers, rows)
        self.preview_label.setText(f"{title} · показано {len(rows)} · номер записи в исходном датасете")

    @staticmethod
    def _replace_table(table, headers, rows):
        model = table.model()
        if isinstance(model, DataModel):
            model.replace(headers, rows)
        else:
            table.setModel(DataModel(headers, rows, table))

    @Slot()
    def reset_preview(self):
        if self.result:
            self.set_preview(list(range(min(1000, self.result.rows))), "Первые записи датасета")

    @Slot()
    def show_affected(self):
        if not self.rule_source_ready() or self._busy:
            return
        row = self.findings_list.currentRow()
        if 0 <= row < len(self.visible_findings):
            f = self.visible_findings[row]
            self.set_preview(f.row_indices, f"{f.title}: первые {min(f.count, 1000)} из {f.count}")
            self.tabs.setCurrentIndex(2)

    @Slot()
    def export(self):
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт полного HTML-отчета", str(Path(self.result.source).with_suffix(".report.html")), "HTML (*.html)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            if not path.lower().endswith(".html"):
                path += ".html"
            try:
                ready = self.rule_source_ready()
                export_html(self.result, path, self.rule_panel.result if ready else None,
                            rule_specs=self.rule_panel.rules if ready else None,
                            review=self.finding_review.review,
                            passport=self.passport_panel.export_content() if ready else None,
                            criteria_result=self.criteria_panel.result if ready else None,
                            criteria_specs=self.criteria_panel.rules if ready else None)
                self.statusBar().showMessage(f"Отчет сохранен: {path}")
            except Exception as exc:
                QMessageBox.warning(self, "Ошибка экспорта", str(exc))

    def _source_signature(self):
        return (self.path, tuple(self.load_options().items()), self.row_limit.value())

    def _rule_source_changed(self, *_):
        if self._loaded_signature is not None and self._loaded_signature != self._source_signature():
            self._loaded_signature = None
            self.rule_panel.invalidate()
            self.criteria_panel.invalidate()
            self.passport_panel.invalidate()
            self._clear_custom_findings()
        self.rule_panel.update_buttons()
        self.criteria_panel.update_buttons()
        self.passport_panel.update_buttons()
        self.finding_review.update_buttons()
        self.affected_button.setEnabled(False)

    def rule_source_ready(self):
        return self.dataset is not None and self._loaded_signature == self._source_signature()

    def source_is_ready(self):
        return self.rule_source_ready()

    def _clear_custom_findings(self):
        if self.result and any(f.code.startswith("custom:") for f in self.result.findings):
            self.result = replace(self.result, findings=[f for f in self.result.findings if not f.code.startswith("custom:")])
            self.finding_review.set_result(self.result, preserve=True)
            self.metrics[3].setText(str(len(self.result.findings)))
            self.filter_findings()

    def _custom_criteria_changed(self):
        self._clear_custom_findings()

    def start_custom_checks(self):
        if self._busy or not self.rule_source_ready() or not self.criteria_panel.rules:
            return
        self.worker = RuleWorker(self.dataset, list(self.criteria_panel.rules), self.example_rows.value(), self)
        self._connect_worker(self.worker, self.on_custom_completed)
        self._begin_worker()
        self.loading_overlay.title.setText("Проверяем ваши условия")

    def on_custom_completed(self, result):
        self.criteria_panel.apply_result(result)
        findings = [f for f in self.result.findings if not f.code.startswith("custom:")]
        findings.extend(evaluations_to_findings(result))
        findings.sort(key=lambda f: (-f.score, f.code, f.columns))
        self.result = replace(self.result, findings=findings)
        self.finding_review.set_result(self.result, preserve=True)
        self.metrics[3].setText(str(len(findings)))
        self.filter_findings()
        self.loading_overlay.title.setText("Ваши проверки завершены")
        self.loading_overlay.stage.setText(f"Условий: {len(result.evaluations)}")
        self.statusBar().showMessage(f"Мои проверки · условий: {len(result.evaluations)} · нарушений: {sum(ev.violations for ev in result.evaluations)}")

    def start_mining(self):
        self._start_rules(None)

    def start_rule_check(self):
        accepted = [r for r in self.rule_panel.rules if r.review_state == "accepted"]
        if accepted:
            self._start_rules(accepted)

    def _start_rules(self, rules):
        if self._busy or not self.rule_source_ready():
            return
        self.worker = RuleWorker(self.dataset, rules, self.example_rows.value(), self)
        self._connect_worker(self.worker, self.on_rules_completed)
        self._begin_worker()
        self.loading_overlay.title.setText("Ищем гипотезы" if rules is None else "Проверяем правила")

    def on_rules_completed(self, result):
        self.rule_panel.apply_result(result)
        self.loading_overlay.title.setText("Поиск правил завершен")
        self.loading_overlay.stage.setText(f"Правил: {len(result.rules)}")
        self.statusBar().showMessage(f"Поиск правил · правил: {len(result.rules)} · записи: {result.rows}")

    def open_rule_demo(self):
        if self._busy:
            return
        from PySide6.QtCore import QStandardPaths
        from .rules.demo import create_rule_demo
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "rule-demo"
        try:
            path = create_rule_demo(folder)
            self._apply_load_options({"separator": None, "encoding": "utf-8-sig", "sheet": None})
            self.tabs.setCurrentIndex(4)
            self._mine_after_analysis = True
            self.select_path(path, auto_start=True)
        except Exception as exc:
            self._mine_after_analysis = False
            QMessageBox.warning(self, "Демо правил не создано", str(exc))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            self.statusBar().showMessage("Дождитесь завершения отмены и закройте окно повторно.")
            event.ignore()
        else:
            self.loading_overlay.reset()
            for card in (self.tour, self.help_prompt):
                if card and card.isVisible():
                    card.reject()
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.setGeometry(self.tabs_frame.geometry())


def run_gui(path: str | None = None, *, max_rows=200000, separator=None, encoding="utf-8-sig", sheet=None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Data X-Ray")
    app.setOrganizationName("DataXRay")
    window = MainWindow()
    window.row_limit.setValue(max_rows)
    for widget, value in ((window.separator, separator), (window.encoding, encoding)):
        index = widget.findData(value)
        if index < 0 and value is not None:
            widget.addItem(str(value), value)
            index = widget.count() - 1
        if index >= 0:
            widget.setCurrentIndex(index)
    window.show()
    if path:
        window.select_path(path)
        if sheet:
            index = window.sheet.findData(sheet)
            if index < 0:
                window.sheet.addItem(sheet, sheet)
                index = window.sheet.count() - 1
            window.sheet.setCurrentIndex(index)
        window.start_analysis()
    QTimer.singleShot(0, window.show_first_run_help)
    return app.exec()
