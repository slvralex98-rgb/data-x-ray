from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QRect, QEvent, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy,
)

from .loaders import excel_sheets


def separator_combo(parent=None):
    widget = QComboBox(parent)
    for label, value in (("Автоматически", None), ("Запятая ,", ","), ("Точка с запятой ;", ";"), ("Табуляция", "\t"), ("Вертикальная черта |", "|")):
        widget.addItem(label, value)
    return widget


def encoding_combo(parent=None):
    widget = QComboBox(parent)
    widget.addItem("UTF-8 / BOM", "utf-8-sig")
    widget.addItem("Windows-1251", "cp1251")
    return widget


def select_value(widget, value):
    index = widget.findData(value)
    if index < 0:
        widget.addItem(str(value), value)
        index = widget.count() - 1
    widget.setCurrentIndex(index)


class ImportDialog(QDialog):
    """Owns draft options; the caller commits them only after acceptance."""
    def __init__(self, path, options=None, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.setWindowTitle("Настройки импорта")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel("Как прочитать датасет")
        title.setObjectName("section")
        layout.addWidget(title)
        filename = QLabel(Path(path).name)
        filename.setWordWrap(True)
        filename.setToolTip(str(path))
        layout.addWidget(filename)
        form = QFormLayout()
        form.setSpacing(14)
        self.separator = separator_combo(self)
        self.encoding = encoding_combo(self)
        self.sheet = QComboBox(self)
        self.sheet.addItem("Первый лист", None)
        options = options or {}
        select_value(self.separator, options.get("separator"))
        select_value(self.encoding, options.get("encoding", "utf-8-sig"))
        extension = Path(path).suffix.lower()
        if extension == ".csv":
            form.addRow("Разделитель", self.separator)
            form.addRow("Кодировка", self.encoding)
            explanation = "CSV читается как текст с заголовком в первой записи. Если столбцы разделились неверно или буквы искажены, измените эти параметры."
        elif extension == ".xlsx":
            for name in excel_sheets(path):
                self.sheet.addItem(name, name)
            select_value(self.sheet, options.get("sheet"))
            form.addRow("Лист книги", self.sheet)
            explanation = "Анализируется один лист с заголовками. Формулы читаются как текст и не вычисляются."
        else:
            explanation = "Parquet сохраняет исходные типы столбцов. Дополнительные параметры чтения для этого формата не нужны."
        # Unused compatibility controls are never shown, including after show().
        self.separator.setVisible(extension == ".csv")
        self.encoding.setVisible(extension == ".csv")
        self.sheet.setVisible(extension == ".xlsx")
        layout.addLayout(form)
        note = QLabel(explanation)
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.buttons = QDialogButtonBox()
        self.accept_button = self.buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        self.accept_button.setObjectName("primary")
        self.buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def options(self):
        return {"separator": self.separator.currentData(), "encoding": self.encoding.currentData(), "sheet": self.sheet.currentData()}


class AnchorPointer(QWidget):
    """Paint only the connector; all interaction passes to the application."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background:transparent;")
        self.start = self.end = QPoint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#ffb776"), 2))
        painter.drawLine(self.start, self.end)
        delta = self.start - self.end
        length = max(1, (delta.x() ** 2 + delta.y() ** 2) ** .5)
        x, y = delta.x() / length, delta.y() / length
        painter.setBrush(QColor("#ffb776"))
        painter.drawPolygon(QPolygon([self.end,
            self.end + QPoint(round(x * 9 - y * 4), round(y * 9 + x * 4)),
            self.end + QPoint(round(x * 9 + y * 4), round(y * 9 - x * 4))]))


class EmbeddedCard(QFrame):
    """An anchored child of centralWidget, never a Qt window or popup."""
    finished = Signal(int)

    def __init__(self, window, target):
        super().__init__(window.centralWidget())
        self.window, self.target = window, target
        self._result = QDialog.DialogCode.Rejected
        self.setObjectName("guidanceCard")
        self.setStyleSheet("QFrame#guidanceCard{background:#252e3b;border:1px solid #ffb776;border-radius:12px;}")
        self.setFixedWidth(390)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pointer = AnchorPointer(self.parentWidget())
        self.highlight = QFrame(self.parentWidget())
        self.highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.highlight.setStyleSheet("background:transparent;border:2px solid #ffb776;border-radius:7px;")
        self.reposition_timer = QTimer(self)
        self.reposition_timer.setSingleShot(True)
        self.reposition_timer.timeout.connect(self.reposition)
        self.parentWidget().installEventFilter(self)
        window.installEventFilter(self)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.parentWidget())
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.reject)
        self.escape.setEnabled(False)

    def eventFilter(self, watched, event):
        if self.isVisible() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.LayoutRequest):
            self.reposition_timer.start(0)
        return super().eventFilter(watched, event)

    def target_rect(self):
        rect = QRect(self.target.mapTo(self.parentWidget(), QPoint()), self.target.size())
        # A large workspace can extend behind its viewport. Point only to the
        # exposed part, including when the user resizes or scrolls a panel.
        ancestor = self.target.parentWidget()
        while ancestor is not None and ancestor is not self.parentWidget():
            rect = rect.intersected(QRect(ancestor.mapTo(self.parentWidget(), QPoint()), ancestor.size()))
            ancestor = ancestor.parentWidget()
        return rect.intersected(self.parentWidget().rect())

    def reposition(self):
        if not self.isVisible():
            return
        area = self.parentWidget().rect().adjusted(12, 12, -12, -12)
        self.setFixedWidth(min(390, area.width()))
        # QLabel's sizeHint is cached across different wrapped texts. Let the
        # actual layout calculate height for this width on every tour step.
        layout = self.layout()
        margins = layout.contentsMargins()
        text_width = self.contentsRect().width() - margins.left() - margins.right()
        for label in self.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if label.wordWrap():
                label.ensurePolished()
                label.setMinimumHeight(label.heightForWidth(text_width))
        layout.invalidate()
        height = layout.totalHeightForWidth(self.width())
        self.resize(self.width(), max(layout.minimumSize().height(), height))
        layout.activate()
        target = self.target_rect()
        width, height = self.width(), self.height()
        gap = 22
        candidates = [QPoint(target.right() + gap, target.center().y() - height // 2),
                      QPoint(target.left() - gap - width, target.center().y() - height // 2),
                      QPoint(target.center().x() - width // 2, target.bottom() + gap),
                      QPoint(target.center().x() - width // 2, target.top() - gap - height)]
        choices = []
        for point in candidates:
            rect = QRect(QPoint(max(area.left(), min(point.x(), area.right() - width + 1)),
                                max(area.top(), min(point.y(), area.bottom() - height + 1))), self.size())
            overlap = rect.intersected(target)
            displacement = (rect.topLeft() - point).manhattanLength()
            choices.append((max(0, overlap.width()) * max(0, overlap.height()), displacement, rect))
        card = min(choices, key=lambda choice: choice[:2])[2]
        self.move(card.topLeft())
        self.highlight.setGeometry(target.adjusted(-3, -3, 3, 3))
        # Connect the closest edges. For a large table the card may overlap a
        # small portion, so point to the nearest exposed target edge.
        if card.right() < target.left():
            start = QPoint(card.right(), max(card.top() + 16, min(target.center().y(), card.bottom() - 16)))
            end = QPoint(target.left() - 4, max(target.top(), min(start.y(), target.bottom())))
        elif card.left() > target.right():
            start = QPoint(card.left(), max(card.top() + 16, min(target.center().y(), card.bottom() - 16)))
            end = QPoint(target.right() + 4, max(target.top(), min(start.y(), target.bottom())))
        elif card.top() > target.bottom():
            start = QPoint(max(card.left() + 16, min(target.center().x(), card.right() - 16)), card.top())
            end = QPoint(start.x(), target.bottom() + 4)
        elif card.bottom() < target.top():
            start = QPoint(max(card.left() + 16, min(target.center().x(), card.right() - 16)), card.bottom())
            end = QPoint(start.x(), target.top() - 4)
        elif card.center().x() < target.center().x():
            start = QPoint(card.right(), max(target.top() + 8, min(card.center().y(), target.bottom() - 8)))
            end = start + QPoint(18, 0)
        else:
            start = QPoint(card.left(), max(target.top() + 8, min(card.center().y(), target.bottom() - 8)))
            end = start - QPoint(18, 0)
        self.pointer.setGeometry(self.parentWidget().rect())
        self.pointer.start, self.pointer.end = start, end
        self.pointer.update()
        self.highlight.show()
        self.highlight.raise_()
        self.pointer.show()
        self.pointer.raise_()
        self.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.escape.setEnabled(True)
        self.reposition()
        self.setFocus()

    def result(self):
        return self._result

    def done(self, result):
        if not self.isVisible():
            return
        self._result = result
        self.hide()
        self.escape.setEnabled(False)
        self.highlight.hide()
        self.pointer.hide()
        self.reposition_timer.stop()
        self.finished.emit(result)

    def accept(self):
        self.done(QDialog.DialogCode.Accepted)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.accept()


class TourDialog(EmbeddedCard):
    def __init__(self, window, settings):
        super().__init__(window, window.open_button)
        self.window, self.settings = window, settings
        self.step = 0
        self.previous_tab = window.tabs.currentIndex()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.counter = QLabel()
        self.counter.setObjectName("muted")
        self.counter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setObjectName("section")
        self.heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.counter)
        layout.addWidget(self.heading)
        layout.addWidget(self.description)
        self.auto_show = QCheckBox("Предлагать обучение при запуске")
        self.auto_show.setChecked(settings.value("help/show_on_startup", True, type=bool))
        layout.addWidget(self.auto_show)
        buttons = QHBoxLayout()
        self.close_button = QPushButton("Закрыть")
        self.back_button = QPushButton("Назад")
        self.next_button = QPushButton("Далее →")
        self.next_button.setObjectName("primary")
        for button in (self.close_button, self.back_button, self.next_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.close_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(lambda: self.go(-1))
        self.next_button.clicked.connect(self.advance)
        self.steps = [
            ("Откройте файл или демо", "Выберите CSV, XLSX или Parquet. В настройках импорта доступны разделитель и кодировка CSV, лист XLSX. Изменить их можно позже. Для знакомства откройте демо.", window.open_button, None),
            ("Выберите глубину анализа", "Лимит записей ограничивает чтение началом файла. Число примеров задает объем пояснений. После изменения настроек повторите анализ.", window.analysis_panel, None),
            ("Запустите анализ", "Нажмите «Анализировать». «Отменить» остановит работу после текущего этапа. При отмене или ошибке предыдущий результат остается доступен.", window.analyze_button, None),
            ("Изучите проблемы", "Выберите строку: справа появятся причина, следующий шаг и примеры. Поиск и приоритет сужают список. Кандидат в ключ или постоянное значение — подсказка, не обязательно ошибка.", window.findings_list, 0),
            ("Отметьте результат проверки", "Задайте статус и нажмите «Применить», добавьте комментарий. «Удалить» скрывает проблему. Для восстановления выберите фильтр «Удаленные» и нужную строку.", window.finding_review.state, 0),
            ("Сохраните или откройте обзор", "Обзор JSON хранит анализ, статусы и комментарии. Открытый снимок показывается отдельно. Применить его отметки можно только к совпадающему текущему анализу; таблица не меняется.", window.finding_review.save_button, 0),
            ("Проверьте исходные записи", "«Показать затронутые записи» откроет до 1 000 строк на вкладке «Данные». Нумерация начинается с 1 после заголовка. «Первые записи датасета» возвращает общий просмотр.", window.affected_button, 0),
            ("Посмотрите профиль и данные", "Профиль содержит типы, пропуски, уникальность, статистику и частые значения столбцов. В разделе «Данные» можно сверить исходные значения.", window.profile_view, 1),
            ("Учитывайте методику", "Здесь описаны настройки завершенного анализа и его ограничения: объем прочитанных данных, трактовка пропусков и принципы проверок.", window.method_view, 3),
            ("Найдите закономерности", "В «Поиске правил» нажмите «Найти правила» или откройте демо правил. Кандидаты описывают закономерности загруженного файла; их нужно проверить по смыслу.", window.rule_panel.mine_button, 4),
            ("Примите или отклоните правило", "Выберите кандидата и изучите примеры. «Принять» включает его в набор, «Отклонить» исключает. «В кандидаты» позволяет пересмотреть решение. Поиск и фильтр помогают найти нарушения.", window.rule_panel.accept_button, 4),
            ("Перенесите набор на другой файл", "Сохраните набор JSON, откройте следующий датасет и набор, затем нажмите «Проверить набор». «Показать нарушения» ведет к исходным строкам. После смены файла требуется новая проверка.", window.rule_panel.check_button, 4),
            ("Создайте свое правило", "Меню «Правило…» позволяет создать, изменить или удалить критерий. Сохраните набор после изменений. Для отдельного списка собственных условий откройте «Мои проверки».", window.rule_panel.rule_button, 4),
            ("Настройте свои проверки", "«Добавить» открывает конструктор: диапазон числа с включенными или строгими границами, обязательность, категории, дата, почта, цифры или регулярное выражение всей строки.", window.criteria_panel.add_button, 6),
            ("Проверьте формат и уникальность", "Для даты выберите формат; для регулярного выражения есть примеры. Уникальность проверяет один столбец или сочетание нескольких. Набор можно изменить, удалить, сохранить, открыть и проверить.", window.criteria_panel.check_button, 6),
            ("Создайте паспорт таблицы", "Паспорт собирает структуру и статистику текущего анализа. «Обновить паспорт» пересоздает его, «Сохранить JSON» сохраняет будущий эталон для сравнения.", window.passport_panel.create_button, 5),
            ("Сравните с эталоном", "Откройте сохраненный паспорт через «Открыть эталон» и нажмите «Сравнить». Изучите изменения структуры и статистики. «Очистить эталон» убирает сравнение.", window.passport_panel.compare_button, 5),
            ("Сохраните отчет и возвращайтесь", "«Экспорт HTML» сохраняет автономный отчет. Переходите между разделами через список слева. Версия приложения указана внизу слева. «Как пользоваться» снова откроет эти подсказки.", window.export_button, 3),
        ]
        self.finished.connect(self.finish_tour)
        self.render_step()

    def render_step(self):
        heading, text, target, tab = self.steps[self.step]
        if tab is not None:
            self.window.tabs.setCurrentIndex(tab)
        self.counter.setText(f"Шаг {self.step + 1} из {len(self.steps)}")
        self.heading.setText(heading)
        self.description.setText(text)
        self.back_button.setEnabled(self.step > 0)
        self.next_button.setText("Готово" if self.step == len(self.steps) - 1 else "Далее →")
        self.target = target
        self.reposition()

    def go(self, offset):
        self.step = max(0, min(len(self.steps) - 1, self.step + offset))
        self.render_step()

    def advance(self):
        if self.step == len(self.steps) - 1:
            self.accept()
        else:
            self.go(1)

    def finish_tour(self, _result):
        self.settings.setValue("help/show_on_startup", self.auto_show.isChecked())
        self.settings.sync()
        self.window.tabs.setCurrentIndex(self.previous_tab)


class HelpPrompt(EmbeddedCard):
    def __init__(self, settings, parent=None):
        super().__init__(parent, parent.help_button)
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        text = QLabel("Показать обучение? Познакомьтесь с анализом, разбором проблем, своими проверками, поиском правил и сравнением паспортов.")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.auto_show = QCheckBox("Предлагать обучение при запуске")
        self.auto_show.setChecked(True)
        layout.addWidget(self.auto_show)
        buttons = QDialogButtonBox()
        buttons.addButton("Не сейчас", QDialogButtonBox.ButtonRole.RejectRole)
        show = buttons.addButton("Показать", QDialogButtonBox.ButtonRole.AcceptRole)
        show.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.finished.connect(self.save_preference)

    def save_preference(self, _result):
        self.settings.setValue("help/show_on_startup", self.auto_show.isChecked())
        self.settings.sync()
