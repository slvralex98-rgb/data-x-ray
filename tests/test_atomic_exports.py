"""A failed export must not destroy the last successfully saved document."""
import io
import os

import polars as pl
import pytest

from data_xray.engine import Analyzer
from data_xray.finding_review import FindingReview, load_review_session, save_review_session
from data_xray.loaders import Dataset
from data_xray.passport import build_passport, load_passport, save_passport
from data_xray.rules import RuleSpec, load_rule_set, save_rule_set


@pytest.fixture(params=["rules", "passport", "review"])
def export(request):
    dataset = Dataset(pl.DataFrame({"x": ["a", "a"]}), "fixture.csv", "csv", False, [])
    result = Analyzer().analyze(dataset)
    if request.param == "rules":
        rules = [RuleSpec("not_null", ("x",), review_state="accepted")]
        return lambda path: save_rule_set(rules, path), load_rule_set
    if request.param == "passport":
        passport = build_passport(dataset, result)
        return lambda path: save_passport(passport, path), load_passport
    review = FindingReview(result)
    review.mark(result.findings[0], "confirmed", "Проверено")
    return lambda path: save_review_session(result, review, path), load_review_session


class PartialWrite:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, payload):
        self.stream.write(payload[:10])
        self.stream.flush()
        raise OSError("simulated partial write")


def test_partial_write_preserves_previous_export(export, tmp_path, monkeypatch):
    save, load = export
    target = tmp_path / "export.json"
    save(target)
    original = target.read_bytes()
    original_open = io.open

    def fail_writing(file, mode="r", *args, **kwargs):
        stream = original_open(file, mode, *args, **kwargs)
        return PartialWrite(stream) if "w" in mode else stream

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", fail_writing)
        with pytest.raises(OSError, match="partial write"):
            save(target)
    assert target.read_bytes() == original
    load(target)
    assert list(tmp_path.iterdir()) == [target]


def test_failed_replace_preserves_previous_export(export, tmp_path, monkeypatch):
    save, load = export
    target = tmp_path / "export.json"
    save(target)
    original = target.read_bytes()

    def fail_replace(source, destination):
        assert os.path.dirname(source) == str(tmp_path)
        assert os.fspath(destination) == str(target)
        raise OSError("simulated replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failure"):
            save(target)
    assert target.read_bytes() == original
    load(target)
    assert list(tmp_path.iterdir()) == [target]


def test_successfully_replaces_existing_file(export, tmp_path):
    save, load = export
    target = tmp_path / "export.json"
    target.write_bytes(b"previous export")
    save(target)
    assert target.read_bytes() != b"previous export"
    load(target)
    original = target.read_bytes()
    save(target)
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]
