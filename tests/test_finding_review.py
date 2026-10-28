import copy
from dataclasses import replace
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import polars as pl
import pytest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QListWidget, QTextBrowser, QFileDialog, QInputDialog

from data_xray.engine import Analyzer
from data_xray.loaders import Dataset
from data_xray.finding_review import (FindingReview, analysis_fingerprint, finding_id,
    save_review_session, load_review_session, render_review_html, export_marked_html)
from data_xray.finding_review_ui import FindingReviewBar


@pytest.fixture
def result():
    return Analyzer().analyze(Dataset(pl.DataFrame({"id": [1, 2, 3], "value": [None, "<script>x</script>", "ok"]}), "fixture.csv", "csv", False, []))


def test_flags_delete_restore_and_strict_roundtrip(result, tmp_path):
    before = copy.deepcopy(result.to_dict())
    review = FindingReview(result)
    f = result.findings[0]
    review.mark(f, "confirmed", "Проверено <img src=x>\nвручную")
    review.remove(f)
    assert f not in review.visible()
    assert review.visible("deleted") == [f]
    assert f in review.visible("all")
    path = tmp_path / "review.json"
    save_review_session(result, review, path)
    snapshot, reopened = load_review_session(path)
    assert snapshot.to_dict() == before
    assert reopened.states == review.states
    assert reopened.get_state(snapshot.findings[0])["deleted"]
    reopened.restore(snapshot.findings[0])
    assert reopened.get_state(snapshot.findings[0])["comment"].startswith("Проверено")
    assert result.to_dict() == before
    assert len(review.get_state(f)) == 3


def test_fingerprint_evidence_and_rebase(result, tmp_path):
    review = FindingReview(result)
    f = result.findings[0]
    review.mark(f, "fixed", "исправление во внешнем файле")
    timestamp_only = replace(result, created_at="другое время", elapsed_seconds=999)
    assert review.matches(timestamp_only)
    other = copy.deepcopy(result)
    other.findings[0].row_indices = [2]
    assert not review.matches(other)
    rebased = review.rebase(other)
    assert rebased.get_state(other.findings[0])["state"] == "unreviewed"
    assert review.rebase(timestamp_only).get_state(f)["state"] == "fixed"
    with pytest.raises(ValueError, match="другому"):
        save_review_session(other, review, tmp_path / "bad.json")
    with pytest.raises(ValueError, match="принадлежит"):
        review.mark(other.findings[0], "confirmed")
    different_source = replace(result, source="another.csv")
    assert analysis_fingerprint(different_source) != analysis_fingerprint(result)
    assert review.rebase(different_source).get_state(f)["state"] == "unreviewed"
    assert finding_id(f) == finding_id(copy.deepcopy(f))


def test_in_place_evidence_change_does_not_inherit_flags(result):
    review = FindingReview(result)
    f = result.findings[0]
    review.mark(f, "confirmed")
    f.description += " Другая оценка."
    fresh = review.rebase(result)
    assert fresh.get_state(f)["state"] == "unreviewed"


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d.update(version=True),
    lambda d: d.update(unexpected="field"),
    lambda d: d.update(fingerprint="0" * 64),
    lambda d: d["analysis"].update(rows=True),
    lambda d: d["analysis"].update(elapsed_seconds=float("inf")),
    lambda d: d["analysis"]["profiles"][0].update(inferred_type="<script>"),
    lambda d: d["analysis"]["findings"][0].update(severity="<script>"),
    lambda d: d["analysis"]["findings"][0].update(row_indices=[999999]),
    lambda d: d["analysis"]["findings"][0].update(row_indices=[0, 0]),
    lambda d: d["analysis"]["findings"][0]["examples"][0].update(row="<img>"),
    lambda d: next(iter(d["review"].values())).update(state="unknown"),
    lambda d: next(iter(d["review"].values())).update(deleted=1),
    lambda d: next(iter(d["review"].values())).update(comment="x" * 10001),
])
def test_reject_invalid_sessions(result, tmp_path, mutation):
    path = tmp_path / "invalid.json"
    save_review_session(result, FindingReview(result), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_review_session(path)


def test_duplicate_keys_excessive_depth_and_bytes_are_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"format":"one","format":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Повторяющееся"):
        load_review_session(path)
    path.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
    with pytest.raises(ValueError):
        load_review_session(path)
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError):
        load_review_session(path)


def test_comment_report_is_escaped_and_export_is_marked_only(result, tmp_path):
    review = FindingReview(result)
    f = result.findings[0]
    review.mark(f, "not_problem", '<script>alert("x")</script>')
    html = render_review_html(result, review)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Сохраненный обзор" in html
    assert "Это не набор правил" in html
    path = tmp_path / "marked.html"
    export_marked_html(result, review, path)
    marked = path.read_text(encoding="utf-8")
    assert marked.count('<article class="finding ') == 1
    with pytest.raises(ValueError):
        review.mark(f, "fixed", "x" * 10001)


class Host(QWidget):
    def __init__(self, result):
        super().__init__()
        self._busy = False
        self.result = result
        self.dataset = object()
        self.visible_findings = []
        layout = QVBoxLayout(self)
        self.bar = FindingReviewBar(self)  # Must work before the list exists.
        layout.addWidget(self.bar)
        self.findings_list = QListWidget()
        layout.addWidget(self.findings_list)
        self.findings_list.currentRowChanged.connect(self.bar.selection_changed)
        self.bar.set_result(result)
        self.filter_findings()

    def filter_findings(self):
        self.findings_list.blockSignals(True)
        self.visible_findings = self.bar.filter(self.result.findings)
        self.findings_list.clear()
        self.findings_list.addItems([f.title for f in self.visible_findings])
        self.findings_list.blockSignals(False)
        self.findings_list.setCurrentRow(0)
        self.bar.selection_changed()


@pytest.fixture
def host(result):
    app = QApplication.instance() or QApplication([])
    host = Host(result)
    host.show()
    app.processEvents()
    yield host
    host.close()
    app.processEvents()


def test_bar_mark_comment_remove_restore_and_busy(host, monkeypatch):
    bar = host.bar
    f = host.visible_findings[0]
    bar.state.setCurrentIndex(bar.state.findData("confirmed"))
    bar.apply_button.click()
    assert bar.review.get_state(f)["state"] == "confirmed"
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a: ("заметка", True))
    bar.comment_button.click()
    assert bar.review.get_state(f)["comment"] == "заметка"
    bar.remove_button.click()
    assert f not in host.visible_findings
    bar.state_filter.setCurrentIndex(bar.state_filter.findData("deleted"))
    assert host.visible_findings == [f]
    assert bar.remove_button.text() == "Восстановить"
    bar.remove_button.click()
    assert host.visible_findings == []
    bar.state_filter.setCurrentIndex(0)
    assert f in host.visible_findings
    host._busy = True
    bar.update_buttons()
    assert not bar.apply_button.isEnabled()
    assert not bar.open_button.isEnabled()
    assert not bar.save_button.isEnabled()


def test_bar_snapshot_never_replaces_current_dataset(host, result, tmp_path, monkeypatch):
    bar = host.bar
    f = host.visible_findings[0]
    bar.review.mark(f, "fixed")
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    bar.save_button.click()
    assert path.is_file()
    original_result, original_dataset, original_review = host.result, host.dataset, bar.review
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    bar.open_button.click()
    assert host.result is original_result
    assert host.dataset is original_dataset
    assert bar.review is original_review
    browser = bar.snapshot_dialog.findChild(QTextBrowser)
    assert "Сохраненный обзор" in browser.toPlainText()
    assert not browser.openLinks()
    assert not browser.openExternalLinks()
    bar.snapshot_dialog.close()
    bar.set_result(result, preserve=True)
    assert bar.review.get_state(f)["state"] == "fixed"
    bar.set_result(result)
    assert bar.review.get_state(f)["state"] == "unreviewed"


def test_snapshot_restore_only_matching_marks_and_busy_guard(host, tmp_path, monkeypatch):
    bar = host.bar
    finding = host.result.findings[0]
    bar.review.mark(finding, "confirmed", "Сохраненная заметка")
    bar.review.remove(finding)
    path = tmp_path / "restore.json"
    save_review_session(host.result, bar.review, path)
    bar.set_result(host.result)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    bar.open_session()
    dialog = bar.snapshot_dialog
    original_result, original_dataset = host.result, host.dataset
    assert dialog.snapshot_apply_button.isEnabled()
    host._busy = True
    bar.update_buttons()
    assert not dialog.snapshot_apply_button.isEnabled()
    bar.apply_snapshot(bar._snapshots[-1][0])
    assert bar.review.get_state(finding)["state"] == "unreviewed"
    host._busy = False
    bar.update_buttons()
    dialog.snapshot_apply_button.click()
    assert bar.review.get_state(finding) == {"state": "confirmed", "comment": "Сохраненная заметка", "deleted": True}
    assert host.result is original_result and host.dataset is original_dataset
    # A dialog may remain open while the active source changes.
    other = replace(host.result, source="other.csv")
    host.result = other
    bar.set_result(other)
    assert not dialog.snapshot_apply_button.isEnabled()
    assert "отличается" in dialog.snapshot_hint.text()
    bar.apply_snapshot(bar._snapshots[-1][0])
    assert bar.review.get_state(finding)["state"] == "unreviewed"
    dialog.close()


def test_snapshot_restore_native_dates_and_source_guard(host, tmp_path, monkeypatch):
    from datetime import date
    result = Analyzer().analyze(Dataset(pl.DataFrame({"day": [date(2024, 2, 29), date(2024, 2, 29), None]}), "native.parquet", "parquet", False, []))
    host.result = result
    host.bar.set_result(result)
    host.filter_findings()
    finding = next(f for f in result.findings if any(isinstance(v, date) for ex in f.examples for v in ex["values"].values()))
    host.bar.review.mark(finding, "fixed", "Дата проверена")
    saved = tmp_path / "dates.json"
    save_review_session(result, host.bar.review, saved)
    snapshot_result, snapshot = load_review_session(saved)
    assert snapshot.matches(result)
    host.bar.set_result(result)
    host.rule_source_ready = lambda: False
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(saved), ""))
    host.bar.open_session()
    dialog = host.bar.snapshot_dialog
    assert not dialog.snapshot_apply_button.isEnabled()
    host.bar.apply_snapshot(snapshot)
    assert host.bar.review.get_state(finding)["state"] == "unreviewed"
    host.rule_source_ready = lambda: True
    host.bar.update_buttons()
    dialog.snapshot_apply_button.click()
    assert host.bar.review.get_state(finding)["state"] == "fixed"
    assert host.result is result
    dialog.close()
