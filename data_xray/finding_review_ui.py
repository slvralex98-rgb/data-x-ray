"""Compact review controls. Imported snapshots open in an isolated report dialog."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFileDialog, QMessageBox, QInputDialog, QDialog, QTextBrowser)

from .finding_review import (FindingReview, STATES, MAX_COMMENT, save_review_session,
                             load_review_session, render_review_html, finding_id)


class FindingReviewBar(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.result = None
        self.review = None
        self.snapshot_dialog = None
        self._snapshots = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        first = QHBoxLayout()
        first.addWidget(QLabel("Статус:"))
        self.state = QComboBox()
        for key, label in STATES.items():
            self.state.addItem(label, key)
        first.addWidget(self.state)
        self.apply_button = QPushButton("Применить")
        self.comment_button = QPushButton("Комментарий…")
        self.remove_button = QPushButton("Удалить")
        for button in (self.apply_button, self.comment_button, self.remove_button):
            first.addWidget(button)
        first.addStretch()
        layout.addLayout(first)
        second = QHBoxLayout()
        self.state_filter = QComboBox()
        self.state_filter.addItem("Активные проблемы", "active")
        for key, label in STATES.items():
            self.state_filter.addItem(label, key)
        self.state_filter.addItem("Удаленные", "deleted")
        self.state_filter.addItem("Все, включая удаленные", "all")
        second.addWidget(self.state_filter)
        self.save_button = QPushButton("Сохранить обзор…")
        self.open_button = QPushButton("Открыть обзор…")
        self.save_button.setToolTip("JSON: весь анализ, статусы и комментарии, включая удаленные проблемы. Данные не изменяются.")
        self.open_button.setToolTip("Открыть сохраненный снимок в отдельном отчете. Текущая таблица не меняется.")
        self.remove_button.setToolTip("Скрыть из активных проблем. Для восстановления выберите фильтр «Удаленные».")
        second.addWidget(self.save_button)
        second.addWidget(self.open_button)
        second.addStretch()
        layout.addLayout(second)
        self.apply_button.clicked.connect(self.apply_state)
        self.comment_button.clicked.connect(self.edit_comment)
        self.remove_button.clicked.connect(self.toggle_deleted)
        self.save_button.clicked.connect(self.save_session)
        self.open_button.clicked.connect(self.open_session)
        self.state_filter.currentIndexChanged.connect(self._refresh)
        self.update_buttons()

    def selected_finding(self):
        listing = getattr(self.host, "findings_list", None)
        visible = getattr(self.host, "visible_findings", [])
        row = listing.currentRow() if listing is not None else -1
        return visible[row] if 0 <= row < len(visible) else None

    def set_result(self, result, preserve=False):
        self.review = (self.review.rebase(result) if preserve and self.review is not None and result is not None
                       else FindingReview(result) if result is not None else None)
        self.result = result
        self.state_filter.blockSignals(True)
        if not preserve:
            self.state_filter.setCurrentIndex(0)
        self.state_filter.blockSignals(False)
        self.update_buttons()

    def filter(self, findings):
        return self.review.visible(self.state_filter.currentData(), findings) if self.review else list(findings)

    def badge(self, finding):
        return self.review.badge(finding) if self.review else STATES["unreviewed"]

    def detail_html(self, finding):
        return self.review.detail_html(finding) if self.review else ""

    def _refresh(self, *_):
        if hasattr(self.host, "findings_list"):
            self.host.filter_findings()
        self.selection_changed()

    def selection_changed(self):
        finding = self.selected_finding()
        if self.review and finding is not None:
            state = self.review.get_state(finding)
            self.state.setCurrentIndex(self.state.findData(state["state"]))
            self.comment_button.setText("Комментарий ✓…" if state["comment"] else "Комментарий…")
            self.remove_button.setText("Восстановить" if state["deleted"] else "Удалить")
        else:
            self.comment_button.setText("Комментарий…")
            self.remove_button.setText("Удалить")
        self.update_buttons()

    def update_buttons(self):
        available = not getattr(self.host, "_busy", False)
        selected = bool(self.review is not None and self.selected_finding() is not None)
        for widget in (self.state, self.apply_button, self.comment_button, self.remove_button):
            widget.setEnabled(available and selected)
        self.state_filter.setEnabled(available and self.review is not None)
        self.save_button.setEnabled(available and self.review is not None)
        self.open_button.setEnabled(available)
        for snapshot, button, hint in self._snapshots:
            matches = self._can_restore(snapshot)
            button.setEnabled(available and matches)
            hint.setText("Можно восстановить только статусы, комментарии и удаления для этого же анализа."
                         if matches else "Отметки не применяются: текущий анализ отличается от сохраненного, отсутствует или источник изменен.")

    def _can_restore(self, snapshot):
        source_ready = getattr(self.host, "rule_source_ready", lambda: True)
        return self.result is not None and source_ready() and snapshot.matches(self.result)

    def apply_state(self):
        finding = self.selected_finding()
        if self.review is not None and finding is not None and not getattr(self.host, "_busy", False):
            self.review.mark(finding, self.state.currentData())
            self._refresh()

    def edit_comment(self):
        finding = self.selected_finding()
        if self.review is None or finding is None or getattr(self.host, "_busy", False):
            return
        previous = self.review.get_state(finding)
        text, ok = QInputDialog.getMultiLineText(self, "Комментарий к проблеме",
            f"Личная заметка (до {MAX_COMMENT:,} символов). Данные не изменяются:", previous["comment"])
        if ok:
            try:
                self.review.mark(finding, previous["state"], text)
                self._refresh()
            except ValueError as exc:
                QMessageBox.warning(self, "Комментарий", str(exc))

    def toggle_deleted(self):
        finding = self.selected_finding()
        if self.review is not None and finding is not None and not getattr(self.host, "_busy", False):
            if self.review.get_state(finding)["deleted"]:
                self.review.restore(finding)
            else:
                self.review.remove(finding)
            self._refresh()

    def save_session(self):
        if self.review is None or getattr(self.host, "_busy", False):
            return
        path, _ = QFileDialog.getSaveFileName(self,
            "Сохранить весь обзор, включая удаленные проблемы", "обзор-data-xray.json", "Обзор Data X-Ray (*.json)")
        if not path:
            return
        try:
            save_review_session(self.result, self.review, path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить обзор", str(exc))

    def open_session(self):
        if getattr(self.host, "_busy", False):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Открыть сохраненный обзор", "", "Обзор Data X-Ray (*.json)")
        if not path:
            return
        try:
            result, review = load_review_session(path)
            markup = render_review_html(result, review)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось открыть обзор", str(exc))
            return
        dialog = QDialog(self.host)
        dialog.setWindowTitle("Сохраненный обзор · Data X-Ray")
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.setHtml(markup)
        layout.addWidget(browser)
        hint = QLabel()
        hint.setWordWrap(True)
        layout.addWidget(hint)
        apply = QPushButton("Применить отметки к текущему анализу")
        # A bound Qt slot avoids retaining the entire host through a lambda cycle.
        apply.clicked.connect(self._apply_from_button)
        layout.addWidget(apply)
        self._snapshots.append((review, apply, hint))
        dialog.snapshot_apply_button = apply
        dialog.snapshot_hint = hint
        self.update_buttons()
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        self.snapshot_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._snapshot_closed)
        dialog.show()

    def _snapshot_closed(self, *_):
        dialog = self.sender()
        self._snapshots = [entry for entry in self._snapshots if entry[1].window() is not dialog]
        if self.snapshot_dialog is dialog:
            self.snapshot_dialog = None

    def _apply_from_button(self, *_):
        button = self.sender()
        snapshot = next((entry[0] for entry in self._snapshots if entry[1] is button), None)
        if snapshot is not None:
            self.apply_snapshot(snapshot)

    def apply_snapshot(self, snapshot):
        # Recheck at click time: an open dialog may outlive the active analysis.
        if getattr(self.host, "_busy", False) or not self._can_restore(snapshot):
            self.update_buttons()
            return
        restored = FindingReview(self.result)
        for finding in self.result.findings:
            state = snapshot.states[finding_id(finding)]
            restored.mark(finding, state["state"], state["comment"])
            if state["deleted"]:
                restored.remove(finding)
        self.review = restored
        self._refresh()
