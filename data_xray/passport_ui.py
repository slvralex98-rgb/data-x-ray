"""Passport workspace. No cell scanning, settings, or secondary raw dataset."""
from copy import deepcopy
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextBrowser, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFileDialog, QMessageBox, QAbstractItemView)

from .passport import build_passport, save_passport, load_passport, compare_passports
from .passport import passport_html, comparison_html
from .passport.html import LABELS
from .theme import DOCUMENT_STYLE


class PassportPanel(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.current = self.baseline = self.comparison = None
        self._dataset = self._result = None
        self.setStyleSheet("QPushButton{padding:7px 10px}")
        layout = QVBoxLayout(self)
        hint = QLabel("Паспорт описывает загруженную таблицу. Сохраните JSON и сравните следующую загрузку с эталоном.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        row = QHBoxLayout()
        for index, (name, label) in enumerate((("create", "Обновить паспорт"), ("save", "Сохранить JSON"),
                            ("open", "Открыть эталон"), ("compare", "Сравнить"),
                            ("clear", "Очистить эталон"))):
            if index == 3:
                layout.addLayout(row)
                row = QHBoxLayout()
            button = QPushButton(label)
            setattr(self, name + "_button", button)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.detail = self._browser()
        self.baseline_detail = self._browser()
        self.comparison_detail = self._browser()
        self.changes_table = QTableWidget(0, 5)
        self.changes_table.setHorizontalHeaderLabels(["Столбец", "Метрика", "Эталон", "Текущий", "Дельта"])
        self.changes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.changes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.changes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.changes_table.horizontalHeader().setStretchLastSection(True)
        self.changes_table.verticalHeader().hide()
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.changes_table)
        split.addWidget(self.comparison_detail)
        split.setSizes([220, 400])
        self.tabs.addTab(self.detail, "Текущий паспорт")
        self.tabs.addTab(self.baseline_detail, "Эталон")
        self.tabs.addTab(split, "Сравнение")
        layout.addWidget(self.tabs, 1)
        self.create_button.clicked.connect(self.create_current)
        self.save_button.clicked.connect(self.save_current)
        self.open_button.clicked.connect(self.open_baseline)
        self.compare_button.clicked.connect(self.compare)
        self.clear_button.clicked.connect(self.clear_baseline)
        self.refresh()

    @staticmethod
    def _browser():
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.document().setDefaultStyleSheet(DOCUMENT_STYLE)
        return browser

    def _ready(self):
        hook = getattr(self.host, "source_is_ready", None) or getattr(self.host, "rule_source_ready", None)
        return bool(hook()) if hook else self._dataset is not None and self._result is not None

    def _available(self):
        return not getattr(self.host, "_busy", False) and self._ready()

    def set_current(self, dataset, result):
        # Called after a completed analysis. Baseline survives a new source.
        passport = build_passport(dataset, result)
        self._dataset, self._result = dataset, result
        self.current, self.comparison = passport, None
        self.refresh()

    def invalidate(self):
        self.current = self.comparison = None
        self._dataset = self._result = None
        self.refresh()

    def update_buttons(self):
        ready = self._available()
        self.create_button.setEnabled(ready and self._dataset is not None)
        self.save_button.setEnabled(ready and self.current is not None)
        idle = not getattr(self.host, "_busy", False)
        self.open_button.setEnabled(idle)
        self.compare_button.setEnabled(ready and self.current is not None and self.baseline is not None)
        self.clear_button.setEnabled(idle and self.baseline is not None)

    def refresh(self):
        self.detail.setHtml(passport_html(self.current))
        self.baseline_detail.setHtml(passport_html(self.baseline) if self.baseline else
            "<h2>Эталон не выбран</h2><p>Откройте ранее сохраненный JSON паспорт. Вторая исходная таблица не нужна.</p>")
        self.comparison_detail.setHtml(comparison_html(self.comparison))
        self.status.setText((f"Текущий: {self.current['rows']:,} строк · {len(self.current['columns'])} столбцов" if self.current else "Текущий паспорт отсутствует") +
            (f" · эталон: {self.baseline['rows']:,} строк" if self.baseline else " · эталон не выбран"))
        self._refresh_changes()
        self.update_buttons()

    def create_current(self):
        if self._available() and self._dataset is not None:
            self.set_current(self._dataset, self._result)

    def save_current(self):
        if not self._available() or self.current is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить паспорт таблицы", "table-passport.json", "Паспорт таблицы (*.json)")
        if path:
            try:
                save_passport(self.current, path)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Не удалось сохранить паспорт", str(exc))

    def open_baseline(self):
        if getattr(self.host, "_busy", False):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Открыть эталонный паспорт", "", "Паспорт таблицы (*.json)")
        if path:
            try:
                self.set_baseline(load_passport(path))
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Не удалось открыть паспорт", str(exc))

    def set_baseline(self, passport):
        from .passport import validate_passport
        validate_passport(passport)
        self.baseline, self.comparison = deepcopy(passport), None
        self.refresh()
        self.tabs.setCurrentIndex(1)

    def compare(self):
        if self._available() and self.current is not None and self.baseline is not None:
            self.comparison = compare_passports(self.current, self.baseline)
            self.refresh()
            self.tabs.setCurrentIndex(2)

    def clear_baseline(self):
        if getattr(self.host, "_busy", False):
            return
        self.baseline = self.comparison = None
        self.refresh()

    def export_content(self):
        return deepcopy({"current": self.current, "baseline": self.baseline, "comparison": self.comparison})

    def _refresh_changes(self):
        rows = []
        if self.comparison:
            c = self.comparison
            rows.append(["Таблица", "Строки", c['rows']['baseline'], c['rows']['current'], c['rows']['delta']])
            rows.extend([[n, "Добавлен", "—", "есть", ""] for n in c['added']])
            rows.extend([[n, "Удален", "есть", "—", ""] for n in c['removed']])
            for item in c['changed']:
                for key, value in item['changes'].items():
                    if key == 'top_values':
                        rows.append([item['column'], LABELS[key], "Покрытие %.1f%%" % (value['baseline_covered_share'] * 100),
                                     "Покрытие %.1f%%" % (value['current_covered_share'] * 100), "См. детали ниже"])
                    else:
                        before, after, delta = value['baseline'], value['current'], value.get('delta')
                        if key.endswith('ratio'):
                            before, after = f'{before:.2%}', f'{after:.2%}'
                            delta = f'{delta * 100:+.2f} п.п.'
                        elif isinstance(delta, (int, float)):
                            delta = f'{delta:+g}'
                        if 'delta_seconds' in value:
                            delta = f'{value["delta_seconds"] / 86400:+g} дней'
                        rows.append([item['column'], LABELS.get(key, key), before, after, delta])
        self.changes_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value) if value is not None else '—'
                cell = QTableWidgetItem(text[:1000])
                cell.setToolTip(text[:4000])
                self.changes_table.setItem(i, j, cell)
