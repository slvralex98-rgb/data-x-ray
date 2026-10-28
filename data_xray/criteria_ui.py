"""Human-readable criteria constructor, shared by checks and rule discovery."""
from html import escape

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QLineEdit, QCheckBox, QLabel, QPushButton, QDialogButtonBox,
    QListWidget, QListWidgetItem, QTextBrowser, QSplitter, QFileDialog, QMessageBox)

from .rules import RuleSpec, save_rule_set, load_rule_set
from .rules.models import MAX_PATTERN
from .rules.numbers import numeric_bound
from .theme import DOCUMENT_STYLE

PRESETS = (
    ('Число в моем диапазоне', 'numeric_constraint'),
    ('Уникальное значение', 'unique'),
    ('Уникальность по нескольким полям', 'unique_multi'),
    ('Обязательное значение', 'not_null'),
    ('Допустимые категории', 'allowed_values'),
    ('Дата в выбранном формате', 'date_format'),
    ('Электронная почта', 'email'),
    ('Только цифры 0–9 (сохраняет нули)', 'digits'),
    ('Мое регулярное выражение', 'regex'),
)
REGEX_PRESETS = (
    ('Выберите пример…', ''),
    ('Код AB-001', '[A-Z]{2}-[0-9]{3}'),
    ('Телефон +7 и 10 цифр', r'\+7[0-9]{10}'),
    ('Почтовый индекс: 6 цифр', '[0-9]{6}'),
    ('Латинские буквы и цифры', '[A-Za-z0-9]+'),
)
EDITABLE_KINDS = {'numeric_constraint', 'not_null', 'allowed_values', 'date_format', 'regex', 'robust_range', 'unique'}


def can_edit_rule(rule):
    return bool(rule and (rule.kind in EDITABLE_KINDS or rule.kind == 'string_shape' and rule.params['format'] in ('email', 'uuid', 'digits')))


class CriteriaDialog(QDialog):
    def __init__(self, columns, parent=None, rule=None):
        super().__init__(parent)
        self.setWindowTitle('Изменить критерий' if rule else 'Новый критерий')
        self.resize(610, 440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.column = QComboBox()
        self.column.setEditable(True)
        self.column.addItems(list(columns))
        self.preset = QComboBox()
        for title, kind in PRESETS:
            self.preset.addItem(title, kind)
        form.addRow('Столбец', self.column)
        form.addRow('Проверка', self.preset)
        self.form = form
        layout.addLayout(form)
        self.unique_box = QWidget()
        uf = QVBoxLayout(self.unique_box)
        uf.setContentsMargins(0, 0, 0, 0)
        uf.addWidget(QLabel('Отметьте поля комбинации (от 2 до 32):'))
        self.unique_columns = QListWidget()
        self.unique_columns.setMaximumHeight(150)
        for column in columns:
            self._add_unique_column(column)
        self.unique_columns.itemChanged.connect(self._unique_selection_changed)
        uf.addWidget(self.unique_columns)
        entry = QHBoxLayout()
        self.unique_name = QLineEdit()
        self.unique_name.setPlaceholderText('Точное имя поля, в том числе до загрузки файла')
        add_column = QPushButton('Добавить поле')
        add_column.clicked.connect(self._add_typed_unique_column)
        entry.addWidget(self.unique_name)
        entry.addWidget(add_column)
        uf.addLayout(entry)
        self.unique_selection = QLabel()
        self.unique_selection.setWordWrap(True)
        uf.addWidget(self.unique_selection)
        layout.addWidget(self.unique_box)
        self._unique_selection_changed()
        self.numeric_box = QWidget()
        nf = QFormLayout(self.numeric_box)
        nf.setContentsMargins(0, 0, 0, 0)
        self.lower, self.upper = QLineEdit(), QLineEdit()
        self.lower.setPlaceholderText('Без нижней границы')
        self.upper.setPlaceholderText('Без верхней границы')
        self.lower_inclusive = QCheckBox('Включая границу (≥)')
        self.upper_inclusive = QCheckBox('Включая границу (≤)')
        for title, edit, include in (('Минимум', self.lower, self.lower_inclusive), ('Максимум', self.upper, self.upper_inclusive)):
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(include)
            nf.addRow(title, row)
        layout.addWidget(self.numeric_box)
        self.values = QLineEdit()
        self.values.setPlaceholderText('Категории через ; например: 001;002;003 (пробелы сохраняются)')
        layout.addWidget(self.values)
        self.date_format = QComboBox()
        self.date_format.setEditable(True)
        for title, fmt in (('ДД.ММ.ГГГГ', '%d.%m.%Y'), ('ГГГГ-ММ-ДД', '%Y-%m-%d'), ('ДД/ММ/ГГГГ', '%d/%m/%Y'), ('Дата и время', '%Y-%m-%d %H:%M:%S')):
            self.date_format.addItem(f'{title}  ({fmt})', fmt)
        layout.addWidget(self.date_format)
        self.date_help = QLabel('Свой формат: %Y — год, %m — месяц, %d — день; время: %H:%M:%S. Две цифры дня и месяца обязательны. Проверяется календарь.')
        self.date_help.setWordWrap(True)
        layout.addWidget(self.date_help)
        self.regex_box = QWidget()
        rf = QVBoxLayout(self.regex_box)
        rf.setContentsMargins(0, 0, 0, 0)
        self.pattern = QLineEdit()
        # Permit one extra character so oversized pasted patterns fail visibly.
        self.pattern.setMaxLength(MAX_PATTERN + 1)
        self.pattern.setPlaceholderText('Например: [A-Z]{2}-[0-9]{3}')
        self.regex_preset = QComboBox()
        for title, pattern in REGEX_PRESETS:
            self.regex_preset.addItem(title, pattern)
        self.regex_preset.currentIndexChanged.connect(lambda: self.pattern.setText(self.regex_preset.currentData()) if self.regex_preset.currentData() else None)
        rf.addWidget(self.regex_preset)
        rf.addWidget(self.pattern)
        syntax = QLabel(f'Совпадение всей строки. Rust regex: [A-Z], [0-9], \\d, \\w, . , +, *, ?, {{n}}, {{n,m}}, группы (…), варианты a|b; до {MAX_PATTERN} символов. Ссылки назад и проверки вперед/назад не поддерживаются.')
        syntax.setWordWrap(True)
        rf.addWidget(syntax)
        layout.addWidget(self.regex_box)
        note = QLabel('Пропуски (NULL, пустоты и маркеры) пропускаются, кроме проверки обязательности. Для обязательного формата добавьте также «Обязательное значение». Заполненное нечисловое значение или невозможная дата — нарушение. Уникальность сравнивает загруженные значения точно: регистр, пробелы и ведущие нули строк сохраняются; повторяющаяся комбинация нарушает правило во всех своих строках.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#ffabb3')
        layout.addWidget(self.error)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Сохранить критерий')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self._preset_changed()
        if rule:
            self._load(rule)

    def _preset_changed(self, *_):
        kind = self.preset.currentData()
        self.numeric_box.setVisible(kind == 'numeric_constraint')
        self.unique_box.setVisible(kind == 'unique_multi')
        self.form.setRowVisible(self.column, kind != 'unique_multi')
        self.values.setVisible(kind == 'allowed_values')
        self.date_format.setVisible(kind == 'date_format')
        self.date_help.setVisible(kind == 'date_format')
        self.regex_box.setVisible(kind == 'regex')
        self.error.clear()

    def _add_unique_column(self, name, checked=False):
        for index in range(self.unique_columns.count()):
            item = self.unique_columns.item(index)
            if item.text() == name:
                if checked:
                    item.setCheckState(Qt.CheckState.Checked)
                return
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.unique_columns.addItem(item)

    def _add_typed_unique_column(self):
        name = self.unique_name.text()
        if not name.strip():
            self.error.setText('Укажите точное имя поля.')
            return
        self._add_unique_column(name, checked=True)
        self.unique_name.clear()
        self._unique_selection_changed()

    def _selected_unique_columns(self):
        return tuple(self.unique_columns.item(i).text() for i in range(self.unique_columns.count())
                     if self.unique_columns.item(i).checkState() == Qt.CheckState.Checked)

    def _unique_selection_changed(self, *_):
        fields = self._selected_unique_columns()
        self.unique_selection.setText('Выбранные поля: ' + (', '.join(f'«{name}»' for name in fields) or 'пока не выбраны'))

    def _load(self, rule):
        if not can_edit_rule(rule):
            raise ValueError('Это найденное правило пока нельзя редактировать в конструкторе.')
        self.column.setCurrentText(rule.columns[0])
        kind = rule.params['format'] if rule.kind == 'string_shape' else 'numeric_constraint' if rule.kind == 'robust_range' else rule.kind
        if kind == 'unique' and len(rule.columns) > 1:
            kind = 'unique_multi'
            for column in rule.columns:
                self._add_unique_column(column, checked=True)
            self._unique_selection_changed()
        if kind == 'uuid':
            # Compatibility option appears only while editing an existing UUID rule.
            self.preset.addItem('UUID (сохраненное правило)', 'uuid')
        self.preset.setCurrentIndex(self.preset.findData(kind))
        p = rule.params
        if kind == 'numeric_constraint':
            for name in ('lower', 'upper'):
                getattr(self, name).setText('' if p[name] is None else str(p[name]))
                getattr(self, name + '_inclusive').setChecked(p.get(name + '_inclusive', True))
        elif kind == 'allowed_values':
            self.values.setText(';'.join(p['values']))
        elif kind == 'date_format':
            index = self.date_format.findData(p['format'])
            if index >= 0:
                self.date_format.setCurrentIndex(index)
            else:
                self.date_format.setCurrentText(p['format'])
        elif kind == 'regex':
            self.pattern.setText(p['pattern'])

    def get_rule(self):
        kind = self.preset.currentData()
        column = self.column.currentText()
        if kind == 'unique_multi':
            columns = self._selected_unique_columns()
            if len(columns) < 2:
                raise ValueError('Для комбинации отметьте не менее двух полей.')
            return RuleSpec('unique', columns, {}, 'accepted')
        if not column.strip():
            raise ValueError('Укажите столбец.')
        params = {}
        if kind == 'numeric_constraint':
            for name in ('lower', 'upper'):
                text = getattr(self, name).text().strip()
                params[name] = numeric_bound(text)
                params[name + '_inclusive'] = getattr(self, name + '_inclusive').isChecked()
        elif kind == 'allowed_values':
            params = {'values': self.values.text().split(';')}
        elif kind == 'date_format':
            index = self.date_format.currentIndex()
            fmt = self.date_format.itemData(index) if index >= 0 and self.date_format.currentText() == self.date_format.itemText(index) else self.date_format.currentText()
            params = {'format': fmt}
        elif kind == 'regex':
            params = {'pattern': self.pattern.text()}
        elif kind in ('email', 'uuid', 'digits'):
            params = {'format': kind}
            kind = 'string_shape'
        return RuleSpec(kind, (column,), params, 'accepted')

    def rule(self):
        return self.get_rule()

    def accept(self):
        try:
            self.get_rule()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class CriteriaPanel(QWidget):
    changed = Signal()

    def __init__(self, host):
        super().__init__()
        self.host, self.rules, self.result = host, [], None
        layout = QVBoxLayout(self)
        hint = QLabel('Задайте критерии для поиска проблем. Правила сохраняются; после смены файла требуется новая проверка.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        for key, title, callback in (
            ('add', 'Добавить', self.add_rule), ('edit', 'Изменить', self.edit_rule), ('delete', 'Удалить', self.delete_rule),
            ('open', 'Открыть', self.open_set), ('save', 'Сохранить', self.save_set), ('check', 'Проверить', host.start_custom_checks)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            setattr(self, key + '_button', button)
            row.addWidget(button)
        layout.addLayout(row)
        self.count = QLabel()
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.detail = QTextBrowser()
        self.detail.document().setDefaultStyleSheet(DOCUMENT_STYLE)
        split.addWidget(self.list)
        split.addWidget(self.detail)
        split.setSizes([300, 500])
        layout.addWidget(split, 1)
        self.list.currentRowChanged.connect(self.show_rule)
        self.refresh()

    def columns(self):
        return self.host.dataset.frame.columns if self.host.rule_source_ready() else []

    def selected(self):
        row = self.list.currentRow()
        return self.rules[row] if 0 <= row < len(self.rules) else None

    def evaluation(self, rule):
        return next((ev for ev in self.result.evaluations if rule and ev.rule.id == rule.id), None) if self.result else None

    def invalidate(self):
        self.result = None
        self.refresh()

    def apply_result(self, result):
        current = {rule.id for rule in self.rules}
        result.evaluations = [ev for ev in result.evaluations if ev.rule.id in current]
        self.result = result
        self.refresh()

    def _changed(self):
        self.invalidate()
        self.changed.emit()

    def add_rule(self):
        dialog = CriteriaDialog(self.columns(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule = dialog.get_rule()
            if all(r.id != rule.id for r in self.rules):
                self.rules.append(rule)
                self._changed()
            self.list.setCurrentRow(next(i for i, r in enumerate(self.rules) if r.id == rule.id))

    def edit_rule(self):
        old = self.selected()
        if not can_edit_rule(old):
            return
        dialog = CriteriaDialog(self.columns(), self, old)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new = dialog.get_rule()
            if new.id != old.id and any(r.id == new.id for r in self.rules):
                QMessageBox.warning(self, 'Критерий уже существует', 'Такой критерий уже есть в наборе.')
                return
            self.rules = [new if r.id == old.id else r for r in self.rules]
            self._changed()
            self.list.setCurrentRow(next(i for i, r in enumerate(self.rules) if r.id == new.id))

    def delete_rule(self):
        rule = self.selected()
        if rule:
            self.rules = [r for r in self.rules if r.id != rule.id]
            self._changed()

    def open_set(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть критерии', '', 'JSON (*.json)', options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            try:
                self.rules = load_rule_set(path)
                self._changed()
            except Exception as exc:
                QMessageBox.warning(self, 'Критерии не открыты', str(exc))

    def save_set(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить критерии', 'criteria.json', 'JSON (*.json)', options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            try:
                save_rule_set(self.rules, path)
            except Exception as exc:
                QMessageBox.warning(self, 'Критерии не сохранены', str(exc))

    def refresh(self):
        old = self.selected()
        self.list.blockSignals(True)
        self.list.clear()
        for rule in self.rules:
            ev = self.evaluation(rule)
            status = 'Не проверено' if ev is None else 'Нет применимых записей' if ev.status == 'not_applicable' else f'Нарушения: {ev.violations} / {ev.evaluated}'
            self.list.addItem(status + '\n' + rule.description)
        self.list.blockSignals(False)
        index = next((i for i, rule in enumerate(self.rules) if old and old.id == rule.id), 0)
        self.list.setCurrentRow(index if self.rules else -1)
        self.count.setText(f'Критериев: {len(self.rules)}' + (f' · проверено: {len(self.result.evaluations)} · записей: {self.result.rows}' if self.result else ' · требуется проверка'))
        self.show_rule()

    def show_rule(self, *_):
        rule = self.selected()
        ev = self.evaluation(rule)
        if rule:
            summary = 'Проверка еще не выполнена.' if ev is None else f'Проверено: {ev.evaluated}; нарушений: {ev.violations}; пропущено: {ev.skipped}. {ev.message}'
            self.detail.setHtml('<h2>Мой критерий</h2><p>' + escape(rule.description) + '</p><p>' + escape(summary) + '</p>')
        else:
            self.detail.setHtml('<h2>Мои проверки</h2><p>Добавьте уникальность значения или комбинации полей, числовой диапазон, формат даты, категории или собственное регулярное выражение.</p>')
        self.update_buttons()

    def update_buttons(self):
        busy, ready = self.host._busy, self.host.rule_source_ready()
        self.add_button.setEnabled(not busy)
        self.open_button.setEnabled(not busy)
        self.edit_button.setEnabled(not busy and can_edit_rule(self.selected()))
        self.delete_button.setEnabled(not busy and self.selected() is not None)
        self.save_button.setEnabled(not busy and bool(self.rules))
        self.check_button.setEnabled(not busy and ready and bool(self.rules))
