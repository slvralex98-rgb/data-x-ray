import copy
import json
import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import polars as pl
import pytest

from data_xray.engine import Analyzer
from data_xray.loaders import Dataset
from data_xray.models import AnalysisOptions
from data_xray.passport import (build_passport, compare_passports, load_passport,
    save_passport, validate_passport, passport_html, comparison_html, render_passport)
from data_xray.passport.core import _fingerprints, MAX_BYTES


def analyzed(data=None, *, source='table.parquet', truncated=False, options=None):
    ds = Dataset(pl.DataFrame(data if data is not None else {'id': [1, 2, 3], 'value': ['a', 'a', None]}), source, 'parquet', truncated, [])
    return ds, Analyzer().analyze(ds, options)


def passport(data=None, **kwargs):
    return build_passport(*analyzed(data, **kwargs))


def rehash(p):
    p['fingerprints'] = _fingerprints(p)
    return p


def test_profiles_null_categories_top_examples_and_original_keys():
    p = passport({'id': ['001', '002', '003', '004', '005', '006'], 'value': [None, '', ' ', 'NULL', 'a1', 'a2']})
    column = p['columns'][1]
    assert (column['nulls'], column['blanks'], column['markers'], column['missing']) == (1, 2, 1, 4)
    assert column['missing_ratio'] == 4 / 6
    assert column['unique_ratio'] == 1
    assert column['sample_patterns'] == [{'pattern': 'A9', 'count': 2}]
    assert p['columns'][0]['inferred_type'] == 'string'
    assert next(f for f in p['evidence'] if f['code'] == 'key')['columns'] == ['id']
    assert all('row_indices' not in f and 'examples' not in f for f in p['evidence'])


def test_numeric_dates_boolean_and_empty_table():
    p = passport({'amount': [1, 3], 'when': ['2025-01-01', '2025-01-03'], 'flag': [True, False]})
    assert p['columns'][0]['statistics']['mean'] == 2
    assert p['columns'][1]['statistics']['max'] == '2025-01-03T00:00:00'
    assert p['columns'][2]['inferred_type'] == 'boolean'
    empty = passport({'id': pl.Series([], dtype=pl.String)})
    assert empty['rows'] == 0
    assert empty['columns'][0]['missing_ratio'] == 0
    assert not empty['evidence']


def test_duplicates_and_composite_keys_reuse_existing_evidence():
    p = passport({'a': [1, 1, 2, 2], 'b': [1, 2, 1, 2]})
    assert p['evidence'][0]['code'] == 'composite_key'
    assert p['evidence'][0]['columns'] == ['a', 'b']
    duplicate = passport({'a': [1, 1, 2]})
    finding = next(f for f in duplicate['evidence'] if f['code'] == 'duplicates')
    assert finding['count'] == 2
    assert 'лишних копий: 1' in finding['description']


def test_build_never_accesses_cells_and_checks_source_metadata():
    ds, result = analyzed()
    frame = SimpleNamespace(height=ds.frame.height, width=ds.frame.width,
                            columns=ds.frame.columns, schema=ds.frame.schema)
    safe = replace(ds, frame=frame)
    assert build_passport(safe, result)['rows'] == 3
    with pytest.raises(ValueError, match='не соответствует'):
        build_passport(replace(ds, source='other'), result)
    with pytest.raises(ValueError, match='не соответствует'):
        build_passport(replace(ds, frame=ds.frame.select('value', 'id')), result)


def test_roundtrip_and_stable_fingerprints_ignore_path_time_notes(tmp_path):
    p = passport()
    path = save_passport(p, tmp_path / 'passport.json')
    assert load_passport(path) == p
    other = passport(source='another/source.parquet')
    other['created_at'] = 'different timestamp'
    other['notes'] = ['different note']
    assert validate_passport(other)
    assert p['fingerprints'] == other['fingerprints']
    assert json.loads(path.read_text('utf-8'))['version'] == 1


def test_fingerprints_change_for_schema_values_rows_and_options():
    base = passport({'x': [1, 2, 2]})
    modified = passport({'x': [1, 1, 2]})
    assert base['fingerprints']['schema'] == modified['fingerprints']['schema']
    assert base['fingerprints']['profile'] != modified['fingerprints']['profile']
    assert base['fingerprints']['schema'] != passport({'y': [1, 2, 2]})['fingerprints']['schema']
    assert base['fingerprints']['profile'] != passport({'x': [1, 2, 2, 2]})['fingerprints']['profile']
    assert base['fingerprints']['profile'] != passport({'x': [1, 2, 2]}, options=AnalysisOptions(dominance=.95))['fingerprints']['profile']


def test_equal_profiles_do_not_prove_equal_cross_column_data():
    a = passport({'id': [1, 2, 3], 'value': ['a', 'b', 'c']})
    b = passport({'id': [1, 2, 3], 'value': ['c', 'a', 'b']})
    assert a['fingerprints'] == b['fingerprints']
    report = comparison_html(compare_passports(a, b))
    assert 'не доказывает равенство данных' in report


def test_schema_order_and_physical_type_differences():
    a = passport({'a': [1, 2], 'b': ['x', 'y']})
    b = passport({'b': ['x', 'y'], 'a': [1.0, 2.0]})
    c = compare_passports(b, a)
    assert c['column_order_changed']
    assert not c['same_schema_fingerprint']
    assert next(x for x in c['changed'] if x['column'] == 'a')['changes']['physical_type'] == {'baseline': 'Int64', 'current': 'Float64'}


def test_directional_comparison_missing_uniqueness_added_removed_ranges():
    old = passport({'id': [1, 2, 3], 'x': [1, 2, 2], 'old': [1, 2, 3]})
    new = passport({'id': [1, 2, 3, 4], 'x': [None, 4, 4, 4], 'new': [1, 2, 3, 4]})
    c = compare_passports(new, old)
    assert c['rows'] == {'baseline': 3, 'current': 4, 'delta': 1}
    assert c['added'] == ['new'] and c['removed'] == ['old']
    x = next(x for x in c['changed'] if x['column'] == 'x')['changes']
    assert x['missing_ratio']['delta'] == .25
    assert x['unique_ratio']['delta'] == pytest.approx(-1 / 3)
    assert x['statistics.max']['delta'] == 2
    assert x['statistics.min']['delta'] == 3


def test_date_range_comparison_exposes_direction_and_seconds():
    old = passport({'when': ['2025-01-01', '2025-01-02']})
    new = passport({'when': ['2025-01-03', '2025-01-04']})
    c = compare_passports(new, old)
    assert c['changed'][0]['changes']['statistics.min']['delta_seconds'] == 2 * 86400
    assert '+2 дней' in comparison_html(c)


def test_top_distribution_absent_values_have_unknown_frequency_not_zero():
    old = passport({'x': ['a', 'a', 'b', 'c', 'd', 'e', 'f']})
    new = passport({'x': ['z', 'z', 'b', 'c', 'd', 'e', 'f']})
    c = compare_passports(new, old)
    distribution = c['changed'][0]['changes']['top_values']
    a = next(v for v in distribution['values'] if v['value'] == 'a')
    assert a['baseline_share'] == 2 / 7
    assert a['current_share'] is None and a['delta'] is None
    assert 0 <= distribution['known_shared_mass'] <= 1
    assert 'не в top-5' in comparison_html(c)


def test_truncation_and_parameters_warn_about_comparability():
    old = passport()
    new = passport(truncated=True, options=AnalysisOptions(null_tokens=('absent',)))
    c = compare_passports(new, old)
    assert any('усечен' in w for w in c['warnings'])
    assert any('Параметры' in w for w in c['warnings'])


def test_extreme_finite_statistics_never_emit_nonfinite_deltas(tmp_path):
    old = passport({'x': [-1e308, -1e308]})
    new = passport({'x': [1e308, 1e308]})
    c = compare_passports(new, old)
    json.dumps(c, allow_nan=False)
    save_passport(new, tmp_path / 'finite.json')
    assert new['columns'][0]['statistics']['median'] is None


def test_long_values_are_clipped_but_distinguished_by_original_hash():
    first = passport({'x': ['a' * 800 + 'X']})
    second = passport({'x': ['a' * 800 + 'Y']})
    item = first['columns'][0]['top_values'][0]
    assert len(item['value']) == 600 and item['truncated']
    assert not first['columns'][0]['sample_patterns']
    assert item['value'] == second['columns'][0]['top_values'][0]['value']
    assert first['fingerprints']['profile'] != second['fingerprints']['profile']


@pytest.mark.parametrize('text', ['NaN', 'Infinity', '-Infinity', '1e999', '[]', '{}',
    '{"version":1,"version":1}', '[' * 2000 + '0' + ']' * 2000])
def test_rejects_invalid_strict_json(tmp_path, text):
    path = tmp_path / 'bad.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        load_passport(path)


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(version=99),
    lambda p: p.update(extra='no'),
    lambda p: p.update(rows=True),
    lambda p: p['columns'][0].update(missing_ratio=.8),
    lambda p: p['columns'][0].update(unique_ratio=float('nan')),
    lambda p: p['columns'][0].update(name='x' * 4001),
    lambda p: p['columns'][0]['statistics'].update(min=100, max=1),
    lambda p: p['columns'][0]['top_values'][0].update(count=-1),
    lambda p: p['columns'][0]['top_values'][0].update(value_hash='abc'),
    lambda p: p['evidence'][0].update(columns=['missing_column']),
    lambda p: p.update(notes=[[[[[[[[[[[[[0]]]]]]]]]]]]]),
])
def test_rejects_malformed_content_even_with_recomputed_fingerprint(mutate):
    p = passport()
    mutate(p)
    with pytest.raises((ValueError, TypeError)):
        rehash(p)
        validate_passport(p)


def test_detects_tampering_and_file_size_cap(tmp_path):
    p = passport()
    p['rows'] = 4
    path = tmp_path / 'changed.json'
    path.write_text(json.dumps(p), encoding='utf-8')
    with pytest.raises(ValueError):
        load_passport(path)
    with path.open('wb') as stream:
        stream.truncate(MAX_BYTES + 1)
    with pytest.raises(ValueError, match='16 МиБ'):
        load_passport(path)


def test_escaped_html_includes_methods_and_details_without_execution():
    p = passport({'<img src=x onerror=alert(1)>': ['<script>alert(1)</script>']}, source='<script>source</script>')
    c = compare_passports(p, p)
    for html in (passport_html(p), comparison_html(c), render_passport({'current': p, 'baseline': p, 'comparison': c})):
        assert '<script>' not in html and '<img src=' not in html
        assert '&lt;script&gt;' in html
    assert 'не хэш всех данных' in passport_html(p)


@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    from data_xray.passport_ui import PassportPanel
    app = QApplication.instance() or QApplication([])
    host = SimpleNamespace(_busy=False, ready=False)
    host.rule_source_ready = lambda: host.ready
    panel = PassportPanel(host)
    yield panel, host
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_panel_lifecycle_preserves_baseline_and_clears_stale_evidence(panel):
    widget, host = panel
    assert not widget.create_button.isEnabled() and widget.open_button.isEnabled()
    ds, result = analyzed()
    host.ready = True
    widget.set_current(ds, result)
    assert widget.save_button.isEnabled()
    widget.set_baseline(widget.current)
    widget.compare()
    assert widget.comparison['same_profile_fingerprint']
    assert widget.changes_table.rowCount() == 1
    baseline = copy.deepcopy(widget.baseline)
    widget.invalidate()
    assert widget.current is None and widget.comparison is None
    assert widget.baseline == baseline
    assert not widget.save_button.isEnabled()
    widget.set_current(*analyzed({'id': [1, 2, 3, 4], 'new': ['a', 'b', 'c', 'd']}))
    assert widget.baseline == baseline and widget.comparison is None
    widget.compare()
    assert widget.comparison['added'] == ['new']
    assert widget.changes_table.rowCount() > 1
    exported = widget.export_content()
    exported['current']['rows'] = 900
    assert widget.current['rows'] == 4
    widget.clear_baseline()
    assert widget.baseline is None and widget.comparison is None


def test_panel_busy_disables_actions_and_does_not_replace_baseline(panel):
    widget, host = panel
    host.ready = True
    widget.set_current(*analyzed())
    widget.set_baseline(widget.current)
    host._busy = True
    widget.update_buttons()
    assert all(not getattr(widget, n + '_button').isEnabled() for n in ('create', 'save', 'open', 'compare', 'clear'))
    widget.clear_baseline()
    widget.compare()
    assert widget.baseline is not None and widget.comparison is None


def test_panel_rejects_invalid_baseline_without_losing_previous(panel):
    widget, host = panel
    host.ready = True
    widget.set_current(*analyzed())
    widget.set_baseline(widget.current)
    baseline = copy.deepcopy(widget.baseline)
    with pytest.raises(ValueError):
        widget.set_baseline({'version': 900})
    assert widget.baseline == baseline
