"""Rule review workspace. Evidence belongs to one committed dataset only."""
from dataclasses import replace
from threading import Event

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QComboBox, QLabel, QListWidget, QListWidgetItem, QTextBrowser,
    QSplitter, QFileDialog, QMessageBox, QMenu, QDialog)

from .checks import AnalysisCancelled
from .rules import RuleMiner, MiningOptions, EvaluationOptions, evaluate_rules, save_rule_set, load_rule_set
from .report import rule_html
from .theme import DOCUMENT_STYLE
from .criteria_ui import CriteriaDialog, can_edit_rule

STATES = {"candidate": "Кандидат", "accepted": "Принято", "rejected": "Отклонено"}


class RuleWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, dataset, rules=None, example_rows=8, parent=None):
        super().__init__(parent)
        self.dataset, self.rules, self.example_rows = dataset, rules, example_rows
        self.cancel_event = Event()

    def run(self):
        try:
            if self.rules is None:
                result = RuleMiner().mine(self.dataset, options=MiningOptions(example_rows=self.example_rows),
                    progress=self.progress.emit, cancel=self.cancel_event)
            else:
                result = evaluate_rules(self.dataset, self.rules, options=EvaluationOptions(example_rows=self.example_rows),
                    progress=self.progress.emit, cancel=self.cancel_event)
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(result)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            # Finished QThreads remain parented to the window for signal identity
            # guards. They must not keep previous Polars tables alive.
            self.dataset = None
            self.rules = None


class RulePanel(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host, self.rules, self.result, self.visible = host, [], None, []
        self._removed_ids = set()
        self.setStyleSheet("QPushButton{padding:7px 10px}")
        layout = QVBoxLayout(self)
        hint = QLabel("Найдите → просмотрите → примите → сохраните. На следующем файле — «Проверить набор».")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        for names in (("Найти правила", "Демо правил", "Открыть набор", "Сохранить набор"),
                      ("Принять", "Отклонить", "В кандидаты", "Проверить набор")):
            row = QHBoxLayout()
            for name in names:
                button = QPushButton(name)
                row.addWidget(button)
                setattr(self, {"Найти правила":"mine_button", "Демо правил":"demo_button", "Открыть набор":"open_button",
                    "Сохранить набор":"save_button", "Принять":"accept_button", "Отклонить":"reject_button",
                    "В кандидаты":"candidate_button", "Проверить набор":"check_button"}[name], button)
            layout.addLayout(row)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по правилу или столбцу…")
        self.state = QComboBox()
        self.state.addItem("Все состояния", None)
        for state, title in STATES.items():
            self.state.addItem(title, state)
        self.state.addItem("С нарушениями", "violations")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state)
        self.rule_button = QPushButton("Правило…")
        menu = QMenu(self.rule_button)
        self.create_action = menu.addAction("Создать свое…", self.create_rule)
        self.edit_action = menu.addAction("Изменить…", self.edit_rule)
        self.delete_action = menu.addAction("Удалить", self.delete_rule)
        self.rule_button.setMenu(menu)
        filters.addWidget(self.rule_button)
        layout.addLayout(filters)
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.document().setDefaultStyleSheet(DOCUMENT_STYLE + "p{margin-top:5px;margin-bottom:5px;line-height:1.3}h2{font-size:18px;margin-top:8px;margin-bottom:6px}th,td{padding:5px}")
        split.addWidget(self.list)
        split.addWidget(self.detail)
        split.setSizes([300, 500])
        layout.addWidget(split, 1)
        self.violations_button = QPushButton("Показать нарушения →")
        layout.addWidget(self.violations_button)
        self.mine_button.clicked.connect(host.start_mining)
        self.demo_button.clicked.connect(host.open_rule_demo)
        self.check_button.clicked.connect(host.start_rule_check)
        self.open_button.clicked.connect(self.open_set)
        self.save_button.clicked.connect(self.save_set)
        self.accept_button.clicked.connect(lambda: self.review("accepted"))
        self.reject_button.clicked.connect(lambda: self.review("rejected"))
        self.candidate_button.clicked.connect(lambda: self.review("candidate"))
        self.violations_button.clicked.connect(self.show_violations)
        self.list.currentRowChanged.connect(self.show_rule)
        self.search.textChanged.connect(self.refresh)
        self.state.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def selected(self):
        row = self.list.currentRow()
        return self.visible[row] if 0 <= row < len(self.visible) else None

    def evaluation(self, rule):
        return next((ev for ev in self.result.evaluations if ev.rule.id == rule.id), None) if self.result and rule else None

    def invalidate(self):
        self.rules = [r for r in self.rules if r.review_state == "accepted"]
        self.result = None
        self.refresh()

    def apply_result(self, result):
        previous = {r.id: r for r in self.rules}
        result.evaluations = [ev for ev in result.evaluations if ev.rule.id not in self._removed_ids]
        result.evaluations = [replace(ev, rule=replace(ev.rule, review_state=previous[ev.rule.id].review_state))
                              if ev.rule.id in previous else ev for ev in result.evaluations]
        found = {r.id for r in result.rules}
        self.rules = result.rules + [r for r in self.rules if r.id not in found]
        self.result = result
        self.refresh()

    def refresh(self, *_):
        previous = self.selected()
        query, state = self.search.text().casefold().strip(), self.state.currentData()
        self.visible = [r for r in self.rules if (not query or query in r.description.casefold()) and
            (not state or r.review_state == state or state == "violations" and self.evaluation(r) and self.evaluation(r).violations)]
        self.visible.sort(key=lambda r: (not bool(self.evaluation(r) and self.evaluation(r).violations), r.kind, r.id))
        self.list.blockSignals(True)
        self.list.clear()
        for rule in self.visible:
            ev = self.evaluation(rule)
            summary = ("Не применимо" if ev.status == "not_applicable" else f"Нарушения: {ev.violations} / {ev.evaluated}") if ev else "Не проверено"
            self.list.addItem(QListWidgetItem(f"{STATES[rule.review_state]} · {summary}\n{rule.description}"))
        self.list.blockSignals(False)
        index = next((i for i, r in enumerate(self.visible) if previous and previous.id == r.id), 0)
        self.list.setCurrentRow(index if self.visible else -1)
        self.count.setText(f"Показано {len(self.visible)} из {len(self.rules)} · принято {sum(r.review_state == 'accepted' for r in self.rules)}" +
            (f" · оценок: {len(self.result.evaluations)} · записей: {self.result.rows}" if self.result else " · требуется проверка набора"))
        self.show_rule()

    def show_rule(self, *_):
        rule = self.selected()
        self.detail.setHtml(rule_html(rule, self.evaluation(rule)) if rule else
            "<h2>Поиск правил</h2><p>Найдите и рассмотрите гипотезы или создайте собственное принятое правило через «Правило…». Для оценки требуется проверка набора.</p>")
        self.update_buttons()

    def update_buttons(self):
        busy = self.host._busy
        ready = self.host.rule_source_ready()
        self.mine_button.setEnabled(not busy and ready)
        self.check_button.setEnabled(not busy and ready and any(r.review_state == "accepted" for r in self.rules))
        self.demo_button.setEnabled(not busy)
        self.open_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and any(r.review_state == "accepted" for r in self.rules))
        self.rule_button.setEnabled(not busy)
        self.create_action.setEnabled(not busy)
        self.edit_action.setEnabled(not busy and can_edit_rule(self.selected()))
        self.delete_action.setEnabled(not busy and self.selected() is not None)
        for button in (self.accept_button, self.reject_button, self.candidate_button):
            button.setEnabled(not busy and self.selected() is not None)
        ev = self.evaluation(self.selected())
        self.violations_button.setEnabled(not busy and ready and bool(ev and ev.row_indices))

    def review(self, state):
        rule = self.selected()
        if rule is None:
            return
        updated = replace(rule, review_state=state)
        self.rules = [updated if r.id == rule.id else r for r in self.rules]
        if self.result:
            self.result.evaluations = [replace(ev, rule=updated) if ev.rule.id == rule.id else ev for ev in self.result.evaluations]
        self.refresh()

    def _rule_columns(self):
        return self.host.dataset.frame.columns if self.host.rule_source_ready() else []

    def create_rule(self):
        dialog = CriteriaDialog(self._rule_columns(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule = dialog.get_rule()
            self._removed_ids.discard(rule.id)
            if all(r.id != rule.id for r in self.rules):
                self.rules.append(rule)
            else:
                # The explicit user condition also accepts an identical mined candidate.
                self.rules = [rule if r.id == rule.id else r for r in self.rules]
                if self.result:
                    self.result.evaluations = [replace(ev, rule=rule) if ev.rule.id == rule.id else ev
                                               for ev in self.result.evaluations]
            self.search.clear()
            self.state.setCurrentIndex(0)
            self.refresh()
            self.list.setCurrentRow(next(i for i, r in enumerate(self.visible) if r.id == rule.id))

    def edit_rule(self):
        old = self.selected()
        if not can_edit_rule(old):
            return
        dialog = CriteriaDialog(self._rule_columns(), self, old)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new = dialog.get_rule()
            if new.id != old.id and any(r.id == new.id for r in self.rules):
                QMessageBox.warning(self, "Правило уже существует", "Такое правило уже есть в наборе.")
                return
            if new.id != old.id:
                self._removed_ids.add(old.id)
            self._removed_ids.discard(new.id)
            self.rules = [new if r.id == old.id else r for r in self.rules]
            if self.result:
                self.result.evaluations = [ev for ev in self.result.evaluations if ev.rule.id != old.id]
            self.search.clear()
            self.state.setCurrentIndex(0)
            self.refresh()
            self.list.setCurrentRow(next(i for i, r in enumerate(self.visible) if r.id == new.id))

    def delete_rule(self):
        rule = self.selected()
        if rule:
            self._removed_ids.add(rule.id)
            self.rules = [r for r in self.rules if r.id != rule.id]
            if self.result:
                self.result.evaluations = [ev for ev in self.result.evaluations if ev.rule.id != rule.id]
            self.refresh()

    def open_set(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть принятый набор", "", "JSON (*.json)", options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            rules = load_rule_set(path)
        except Exception as exc:
            QMessageBox.warning(self, "Набор не открыт", str(exc))
            return
        self.rules, self.result = rules, None
        self._removed_ids.clear()
        self.refresh()

    def save_set(self):
        accepted = [r for r in self.rules if r.review_state == "accepted"]
        if not accepted:
            return
        path, _ = QFileDialog.getSaveFileName(self, f"Сохранить принятых правил: {len(accepted)}", "rules.json", "JSON (*.json)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            try:
                save_rule_set(accepted, path)
                self.host.statusBar().showMessage(f"Сохранено принятых правил: {len(accepted)}")
            except Exception as exc:
                QMessageBox.warning(self, "Набор не сохранен", str(exc))

    def show_violations(self):
        ev = self.evaluation(self.selected())
        if ev and ev.row_indices and self.host.rule_source_ready():
            self.host.set_preview(ev.row_indices, f"Нарушения правила: {ev.violations}; показаны первые до 1 000")
            self.host.tabs.setCurrentIndex(2)
